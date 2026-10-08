"""Train the ear condition classifier from the prepared manifest.

Every image first passes through ear_detection.imaging.normalize, the same
function the service applies at inference, and the result is cached as a
lossless PNG. The fixed circular field of view it imposes is what stops the
network from identifying the source dataset instead of the ear.

Training runs in two phases. The backbone is frozen while the new head
learns, then the upper backbone is unfrozen at a much lower learning rate on
a cosine schedule. BatchNorm layers stay in inference mode throughout:
MobileNetV2 has 52 of them, and letting their running statistics drift on
small CPU batches destroys the pretrained features.

The checkpoint kept is the one with the best validation macro F1, not
accuracy. Classes run from about a thousand training images down to twenty,
so accuracy would be dominated by the majority class.

Usage:
    python scripts/train.py [--epochs-head N] [--epochs-finetune N]
"""

import argparse
import csv
import json
import os
import sys
import time

import numpy as np
import tensorflow as tf
from PIL import Image
from tensorflow import keras
from tensorflow.keras import layers

from common import ARTIFACTS, BASE_DIR, LABELS, MANIFEST, NORMALIZED_DIR, normalized_path
from ear_detection import imaging

INPUT_STATS = os.path.join(ARTIFACTS, "input_stats.json")
MODEL_OUT = os.path.join(BASE_DIR, "models", "ear_classifier.keras")

SIZE = imaging.INPUT_SIZE
BATCH_SIZE = 16          # 4 CPU cores and limited free RAM
SEED = 1337
UNFREEZE_FROM = "block_9_expand"


def build_normalized_cache(rows):
    """Normalise every image once and record input statistics.

    The brightness and sharpness ranges of the training split are saved so
    the service can warn when an upload falls outside what the model saw.
    """
    os.makedirs(NORMALIZED_DIR, exist_ok=True)
    train_stats = {"brightness": [], "sharpness": []}
    started = time.time()

    for i, row in enumerate(rows):
        target = normalized_path(row["path"])
        if os.path.isfile(target):
            array = np.asarray(Image.open(target).convert("RGB"), dtype=np.uint8)
        else:
            source = os.path.join(BASE_DIR, row["path"])
            array = imaging.normalize(imaging.open_image(source))
            Image.fromarray(array).save(target, optimize=False)

        if row["split"] == "train":
            stats = imaging.input_statistics(array)
            train_stats["brightness"].append(stats["brightness"])
            train_stats["sharpness"].append(stats["sharpness"])

        if (i + 1) % 500 == 0:
            print("  normalised %d / %d  (%.0fs)" % (i + 1, len(rows), time.time() - started))

    summary = {}
    for key, values in train_stats.items():
        values = np.asarray(values)
        summary[key] = {
            "p01": float(np.percentile(values, 1)),
            "p05": float(np.percentile(values, 5)),
            "median": float(np.median(values)),
            "p95": float(np.percentile(values, 95)),
            "p99": float(np.percentile(values, 99)),
        }
    with open(INPUT_STATS, "w", encoding="utf-8") as fh:
        json.dump(summary, fh, indent=2)
    print("  input ranges written to", INPUT_STATS)


def build_dataset(rows, class_to_index, split, augment):
    subset = [r for r in rows if r["split"] == split]
    paths = [normalized_path(r["path"]) for r in subset]
    labels = [class_to_index[r["label"]] for r in subset]

    ds = tf.data.Dataset.from_tensor_slices((paths, labels))
    if augment:
        ds = ds.shuffle(len(subset), seed=SEED, reshuffle_each_iteration=True)

    def decode(path, label):
        image = tf.io.decode_png(tf.io.read_file(path), channels=3)
        image = tf.cast(image, tf.float32)
        image.set_shape([SIZE, SIZE, 3])
        return image, label

    ds = ds.map(decode, num_parallel_calls=tf.data.AUTOTUNE)

    if augment:
        mask = tf.constant(imaging.fov_mask()[..., None], dtype=tf.float32)
        # Otoscope orientation is arbitrary, so any angle is label preserving.
        # The circle is invariant under rotation, so nothing outside it leaks in.
        rotate = layers.RandomRotation(0.5, fill_mode="constant", fill_value=0.0, seed=SEED)
        # Zoom in only. Zooming out would draw a black ring inside the circle,
        # a pattern the service never produces.
        zoom = layers.RandomZoom((-0.12, 0.0), fill_mode="constant", fill_value=0.0, seed=SEED)

        def augmentation(image, label):
            image = tf.image.random_flip_left_right(image)
            image = rotate(image, training=True)
            image = zoom(image, training=True)
            # Illumination varies widely between devices and operators.
            image = tf.image.random_brightness(image, 20.0)
            image = tf.image.random_contrast(image, 0.85, 1.15)
            # Colour is diagnostic (erythema, debris), so hue moves only slightly.
            image = tf.image.random_saturation(image, 0.9, 1.1)
            image = tf.image.random_hue(image, 0.02)
            # Restore the exact field of view the service will present.
            image = tf.clip_by_value(image, 0.0, 255.0) * mask
            return image, label

        ds = ds.map(augmentation, num_parallel_calls=tf.data.AUTOTUNE)

    ds = ds.map(
        lambda x, y: (keras.applications.mobilenet_v2.preprocess_input(x), y),
        num_parallel_calls=tf.data.AUTOTUNE,
    )
    return ds.batch(BATCH_SIZE).prefetch(tf.data.AUTOTUNE), len(subset), labels


def set_backbone_trainable(backbone, trainable, from_layer=None):
    """Unfreeze the backbone from a layer onwards, never its BatchNorms."""
    started = from_layer is None
    for layer in backbone.layers:
        if from_layer is not None and layer.name == from_layer:
            started = True
        if isinstance(layer, layers.BatchNormalization):
            layer.trainable = False
        else:
            layer.trainable = trainable and started


class ValidationTracker(keras.callbacks.Callback):
    """Validation macro F1 and referral sensitivity, with best-weight tracking."""

    def __init__(self, val_ds, num_classes, normal_index):
        super().__init__()
        self.val_ds = val_ds
        self.num_classes = num_classes
        self.normal_index = normal_index
        self.best = -1.0
        self.best_weights = None
        self.best_epoch = None
        self.history = []

    def on_epoch_end(self, epoch, logs=None):
        true, pred = [], []
        for batch_x, batch_y in self.val_ds:
            probs = self.model(batch_x, training=False).numpy()
            pred.extend(np.argmax(probs, axis=1))
            true.extend(batch_y.numpy())
        true, pred = np.array(true), np.array(pred)

        f1s = []
        for c in range(self.num_classes):
            tp = np.sum((pred == c) & (true == c))
            fp = np.sum((pred == c) & (true != c))
            fn = np.sum((pred != c) & (true == c))
            precision = tp / (tp + fp) if tp + fp else 0.0
            recall = tp / (tp + fn) if tp + fn else 0.0
            f1s.append(2 * precision * recall / (precision + recall) if precision + recall else 0.0)
        macro = float(np.mean(f1s))

        diseased = true != self.normal_index
        sensitivity = float(np.mean(pred[diseased] != self.normal_index))

        self.history.append({"macro_f1": macro, "referral_sensitivity": sensitivity})
        marker = ""
        if macro > self.best:
            self.best = macro
            self.best_weights = self.model.get_weights()
            self.best_epoch = len(self.history)
            marker = "  <- best"
        print("    epoch %02d  val_macro_f1 %.4f  referral_sensitivity %.3f%s"
              % (epoch + 1, macro, sensitivity, marker), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs-head", type=int, default=10)
    parser.add_argument("--epochs-finetune", type=int, default=18)
    args = parser.parse_args()

    keras.utils.set_random_seed(SEED)

    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    with open(LABELS, encoding="utf-8") as fh:
        meta = json.load(fh)
    classes = meta["classes"]
    class_to_index = meta["class_to_index"]
    num_classes = len(classes)

    print("=== normalising images ===", flush=True)
    build_normalized_cache(rows)

    train_ds, n_train, train_labels = build_dataset(rows, class_to_index, "train", True)
    val_ds, n_val, _ = build_dataset(rows, class_to_index, "val", False)
    print("classes %d   train %d   val %d" % (num_classes, n_train, n_val), flush=True)

    # Square root inverse frequency. Full inverse frequency gave the smallest
    # class a weight near 14 and made the model shout it at normal ears.
    counts = np.bincount(train_labels, minlength=num_classes).astype(np.float64)
    raw = np.sqrt(counts.max() / np.maximum(counts, 1))
    raw = raw / (np.sum(counts * raw) / n_train)
    class_weight = {i: float(raw[i]) for i in range(num_classes)}
    print("class weights:", {classes[i]: round(w, 2) for i, w in class_weight.items()}, flush=True)

    backbone = keras.applications.MobileNetV2(
        input_shape=(SIZE, SIZE, 3), include_top=False, weights="imagenet",
    )
    set_backbone_trainable(backbone, False)

    inputs = keras.Input(shape=(SIZE, SIZE, 3), name="image")
    x = backbone(inputs, training=False)
    x = layers.GlobalAveragePooling2D(name="pool")(x)
    x = layers.Dropout(0.3, name="dropout")(x)
    outputs = layers.Dense(num_classes, activation="softmax", name="predictions")(x)
    model = keras.Model(inputs, outputs, name="ear_classifier")

    tracker = ValidationTracker(val_ds, num_classes, class_to_index["normal"])

    print("\n=== phase 1: frozen backbone ===", flush=True)
    model.compile(
        optimizer=keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.fit(train_ds, validation_data=val_ds, epochs=args.epochs_head,
              class_weight=class_weight, callbacks=[tracker], verbose=2)

    print("\n=== phase 2: fine tune from %s ===" % UNFREEZE_FROM, flush=True)
    set_backbone_trainable(backbone, True, from_layer=UNFREEZE_FROM)
    trainable = sum(1 for l in backbone.layers if l.trainable)
    print("backbone layers trainable: %d of %d (BatchNorm held frozen)"
          % (trainable, len(backbone.layers)), flush=True)

    steps = int(np.ceil(n_train / BATCH_SIZE)) * max(args.epochs_finetune, 1)
    schedule = keras.optimizers.schedules.CosineDecay(3e-5, decay_steps=steps, alpha=0.03)
    model.compile(
        optimizer=keras.optimizers.Adam(schedule),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.fit(train_ds, validation_data=val_ds, epochs=args.epochs_finetune,
              class_weight=class_weight, callbacks=[tracker], verbose=2)

    if tracker.best_weights is not None:
        model.set_weights(tracker.best_weights)
        print("\nrestored best weights from epoch %d, val_macro_f1 %.4f"
              % (tracker.best_epoch, tracker.best), flush=True)

    os.makedirs(os.path.dirname(MODEL_OUT), exist_ok=True)
    model.save(MODEL_OUT)

    # The class order travels with the weights. Not recording it is what made
    # the original model unusable.
    with open(os.path.join(ARTIFACTS, "training_summary.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "classes": classes,
            "backbone": "MobileNetV2 (ImageNet)",
            "unfrozen_from": UNFREEZE_FROM,
            "best_epoch": tracker.best_epoch,
            "best_val_macro_f1": tracker.best,
            "history": tracker.history,
            "train_images": n_train,
            "val_images": n_val,
            "input_size": SIZE,
            "preprocessing": "imaging.normalize then mobilenet_v2.preprocess_input",
            "class_weight": "square root inverse frequency",
            "trained_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        }, fh, indent=2)

    print("saved model to", MODEL_OUT, flush=True)


if __name__ == "__main__":
    sys.exit(main())
