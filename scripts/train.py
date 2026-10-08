"""Train the ear condition classifier from the prepared manifest.

Two phases. The backbone is frozen while the new head learns, then the top of
the backbone is unfrozen at a much lower learning rate. BatchNorm layers stay
in inference mode throughout: MobileNetV2 has 52 of them, and letting their
running statistics drift on small CPU batches destroys the pretrained features.

Selection is on macro F1, not accuracy. The classes run from 1013 training
images down to 20, so accuracy would be dominated by the majority class.

Usage:
    python scripts/train.py [--epochs-head N] [--epochs-finetune N]
"""

import argparse
import csv
import json
import os
import sys

import numpy as np
import tensorflow as tf
from tensorflow import keras
from tensorflow.keras import layers

BASE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
ARTIFACTS = os.path.join(BASE_DIR, "artifacts")
MANIFEST = os.path.join(ARTIFACTS, "manifest.csv")
LABELS = os.path.join(ARTIFACTS, "labels.json")
MODEL_OUT = os.path.join(BASE_DIR, "models", "ear_classifier.keras")
CACHE_DIR = os.path.join(ARTIFACTS, "cache")

IMAGE_SIZE = (224, 224)
BATCH_SIZE = 16          # kept small: 4 CPU cores and limited free RAM
SEED = 1337


def load_manifest():
    with open(MANIFEST, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    with open(LABELS, encoding="utf-8") as fh:
        meta = json.load(fh)
    return rows, meta


def build_dataset(rows, class_to_index, split, augment, cache_name):
    subset = [r for r in rows if r["split"] == split]
    paths = [os.path.join(BASE_DIR, r["path"]) for r in subset]
    labels = [class_to_index[r["label"]] for r in subset]

    ds = tf.data.Dataset.from_tensor_slices((paths, labels))

    def decode(path, label):
        data = tf.io.read_file(path)
        # channels=3 normalises greyscale and RGBA inputs alike
        image = tf.io.decode_image(data, channels=3, expand_animations=False)
        image = tf.image.resize(image, IMAGE_SIZE)
        image.set_shape([IMAGE_SIZE[0], IMAGE_SIZE[1], 3])
        return image, label

    ds = ds.map(decode, num_parallel_calls=tf.data.AUTOTUNE)

    # Cache the decoded and resized images on disk so JPEG decoding happens
    # once rather than every epoch. Caching before augmentation keeps the
    # random transforms varying across epochs.
    os.makedirs(CACHE_DIR, exist_ok=True)
    ds = ds.cache(os.path.join(CACHE_DIR, cache_name))

    if augment:
        ds = ds.shuffle(min(len(subset), 1000), seed=SEED, reshuffle_each_iteration=True)

        def augmentation(image, label):
            # Otoscope orientation is arbitrary and either ear may be imaged,
            # so full rotation and mirroring are both label preserving.
            image = tf.image.random_flip_left_right(image)
            image = tf.image.rot90(image, k=tf.random.uniform([], 0, 4, dtype=tf.int32))
            # Illumination varies widely between devices and operators.
            image = tf.image.random_brightness(image, 25.0)
            image = tf.image.random_contrast(image, 0.85, 1.15)
            # Colour carries diagnostic signal, so hue is nudged only slightly.
            image = tf.image.random_hue(image, 0.02)
            image = tf.image.random_saturation(image, 0.9, 1.1)
            return tf.clip_by_value(image, 0.0, 255.0), label

        ds = ds.map(augmentation, num_parallel_calls=tf.data.AUTOTUNE)

    # Must match what the service applies at inference time.
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


class MacroF1(keras.callbacks.Callback):
    """Macro F1 on the validation set, plus best-weight tracking."""

    def __init__(self, val_ds, num_classes):
        super().__init__()
        self.val_ds = val_ds
        self.num_classes = num_classes
        self.best = -1.0
        self.best_weights = None
        self.history = []

    def on_epoch_end(self, epoch, logs=None):
        true, pred = [], []
        for batch_x, batch_y in self.val_ds:
            probs = self.model(batch_x, training=False)
            pred.extend(np.argmax(probs.numpy(), axis=1))
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
        logs = logs or {}
        logs["val_macro_f1"] = macro
        self.history.append(macro)
        if macro > self.best:
            self.best = macro
            self.best_weights = self.model.get_weights()
            marker = "  <- best"
        else:
            marker = ""
        print("    epoch %02d  val_macro_f1 %.4f%s" % (epoch + 1, macro, marker))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--epochs-head", type=int, default=12)
    parser.add_argument("--epochs-finetune", type=int, default=14)
    args = parser.parse_args()

    keras.utils.set_random_seed(SEED)

    rows, meta = load_manifest()
    classes = meta["classes"]
    class_to_index = meta["class_to_index"]
    num_classes = len(classes)

    train_ds, n_train, train_labels = build_dataset(rows, class_to_index, "train", True, "train")
    val_ds, n_val, _ = build_dataset(rows, class_to_index, "val", False, "val")

    print("classes %d   train %d   val %d" % (num_classes, n_train, n_val))

    # Inverse frequency weighting so the rare classes are not ignored.
    counts = np.bincount(train_labels, minlength=num_classes).astype(np.float64)
    class_weight = {
        i: float(n_train / (num_classes * counts[i])) if counts[i] else 0.0
        for i in range(num_classes)
    }
    print("class weights:", {classes[i]: round(w, 2) for i, w in class_weight.items()})

    backbone = keras.applications.MobileNetV2(
        input_shape=(IMAGE_SIZE[0], IMAGE_SIZE[1], 3),
        include_top=False,
        weights="imagenet",
    )
    set_backbone_trainable(backbone, False)

    inputs = keras.Input(shape=(IMAGE_SIZE[0], IMAGE_SIZE[1], 3))
    x = backbone(inputs, training=False)
    x = layers.GlobalAveragePooling2D()(x)
    x = layers.Dropout(0.3)(x)
    outputs = layers.Dense(num_classes, activation="softmax", name="predictions")(x)
    model = keras.Model(inputs, outputs, name="ear_classifier")

    tracker = MacroF1(val_ds, num_classes)

    print("\n=== phase 1: frozen backbone ===")
    model.compile(
        optimizer=keras.optimizers.Adam(1e-3),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=args.epochs_head,
        class_weight=class_weight,
        callbacks=[tracker],
        verbose=2,
    )

    print("\n=== phase 2: fine tune top of backbone ===")
    set_backbone_trainable(backbone, True, from_layer="block_12_expand")
    trainable = sum(1 for l in backbone.layers if l.trainable)
    print("backbone layers trainable: %d of %d (BatchNorm held frozen)"
          % (trainable, len(backbone.layers)))

    model.compile(
        optimizer=keras.optimizers.Adam(1e-5),
        loss="sparse_categorical_crossentropy",
        metrics=["accuracy"],
    )
    model.fit(
        train_ds,
        validation_data=val_ds,
        epochs=args.epochs_finetune,
        class_weight=class_weight,
        callbacks=[tracker],
        verbose=2,
    )

    if tracker.best_weights is not None:
        model.set_weights(tracker.best_weights)
        print("\nrestored best weights, val_macro_f1 %.4f" % tracker.best)

    os.makedirs(os.path.dirname(MODEL_OUT), exist_ok=True)
    model.save(MODEL_OUT)

    # The class order travels with the weights. Not recording this is what
    # made the previous model unusable.
    with open(os.path.join(ARTIFACTS, "training_summary.json"), "w", encoding="utf-8") as fh:
        json.dump({
            "classes": classes,
            "best_val_macro_f1": tracker.best,
            "macro_f1_history": tracker.history,
            "train_images": n_train,
            "val_images": n_val,
            "image_size": list(IMAGE_SIZE),
            "preprocessing": "mobilenet_v2.preprocess_input",
        }, fh, indent=2)

    print("saved model to", MODEL_OUT)


if __name__ == "__main__":
    sys.exit(main())
