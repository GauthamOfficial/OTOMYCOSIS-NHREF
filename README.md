# Ear Disease Detection System

**Nawaloka Hospital PLC, Research and Education Foundation (NHREF)**

A screening aid that reads a single otoscope image of the tympanic membrane and
recommends whether the ear should be referred for ENT review. It also names the
most likely of twelve conditions, shows the full calibrated differential, and
highlights the regions of the image the model relied on.

> **Research use only.** This is not a medical device and has not been
> prospectively validated. Every result must be reviewed by a qualified ENT
> practitioner, and the system must never be used to withhold care.

![Screening interface](docs/images/screening-result.png)

## At a glance

Measured on 718 held out test images that were never used for training, model
selection or calibration:

| Measure | Result | 95% CI |
| --- | --- | --- |
| Abnormal ears referred (sensitivity) | **97.0%** | 95.1 to 98.2% |
| Normal ears not referred (specificity) | **75.6%** | 69.4 to 80.8% |
| AUROC, abnormal vs normal | 0.974 | |
| 12 condition macro F1 | 0.686 | |

Of 501 abnormal test ears, 15 were not referred; 11 of those were
myringosclerosis, which is usually benign. No cholesteatoma was missed. The full
breakdown, including every operating point, per condition results and the
limitations below, is in the [validation report](docs/VALIDATION_REPORT.md),
which is generated from the model on disk and also served by the application.

## Quick start on Windows

You need **Python 3.11** (3.9 and 3.10 also work; 3.12 and later do not, because
TensorFlow 2.15 does not support them). Install it from
[python.org](https://www.python.org/downloads/release/python-3119/) and tick
*Add python.exe to PATH*.

1. Double click **`setup.bat`**. It creates a private environment and installs
   the dependencies. TensorFlow is about 300 MB, so allow a few minutes.
2. Double click **`start.bat`**. The model loads in a few seconds and the
   interface opens at <http://localhost:5000>.
3. Drop an otoscope image onto the page and press **Run Analysis**.

If you have the dataset, `python scripts/export_samples.py` writes one held
out test image per condition into `samples/` for a quick demonstration.

On macOS or Linux:

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py --open
```

## What the interface shows

| Section | Meaning |
| --- | --- |
| **Screening recommendation** | *Refer for ENT review* or *No abnormality detected*, with the probability of an abnormal ear drawn against the referral threshold. |
| **Most likely finding** | The leading condition, its calibrated probability and a one line description. Marked *low confidence* when the top two conditions are close. |
| **Chronic group** | When a chronic otitis media subtype is involved, the combined probability across all five chronic subtypes, which is more reliable than any one subtype. |
| **Differential** | Every condition, ranked, with calibrated probabilities. |
| **Model attention** | The image exactly as the model received it, and the same image with the regions that drove the finding tinted blue. |
| **Validation** | The headline test results and a link to the full report. |

Uncertainty always resolves toward referral. If the most likely finding is a
normal ear but the model is not confident enough to rule disease out, it still
recommends referral and says so. **Print result** produces a one page summary
for the patient record, with the recommendation first.

## Conditions recognised

| Condition | Description |
| --- | --- |
| Acute Otitis Media | Acute middle ear infection; bulging, erythematous tympanic membrane. |
| Otitis Media with Effusion | Fluid behind an intact tympanic membrane without acute infection. |
| Chronic Otitis Media | Chronic middle ear disease; subtype not recorded in the source data. |
| Chronic Suppurative OM | Persistent perforation with chronic discharge. |
| Chronic OM, Active, No Cholesteatoma | Perforation with active discharge and no cholesteatoma. |
| Chronic OM, Inactive | Dry tympanic membrane perforation without discharge. |
| Chronic OM with Cholesteatoma | Keratinising squamous epithelium in the middle ear; erosive. |
| Myringosclerosis | Calcified white plaques in the tympanic membrane; includes tympanosclerosis. |
| Cerumen Impaction | Ear wax occluding the canal or obscuring the tympanic membrane. |
| Otitis Externa | Inflammation of the external ear canal. |
| Other Findings | Foreign body, pseudomembrane or ventilation tube, pooled for lack of images. |
| Normal Ear | No abnormality detected. |

Otomycosis is **not** among them: the dataset contains no otomycosis images.

## How it works

1. **Field of view normalisation.** The image is cropped to its illuminated
   field, squared, resized to 224 pixels and masked to one fixed circle. The
   training data merges three source datasets that frame the ear differently;
   without this step the network learned to recognise the dataset rather than
   the ear. The same function runs at training and at inference.
2. **Classification.** MobileNetV2, initialised from ImageNet and fine tuned
   on 3,326 training images. It is small enough to run on an ordinary laptop
   CPU in about a third of a second.
3. **Orientation averaging.** The prediction is averaged over all eight
   rotations and reflections of the image, so the result does not depend on
   how the otoscope was held. This is verified by the test suite.
4. **Calibration.** Probabilities are temperature scaled so that a stated
   confidence matches an observed frequency.
5. **Referral threshold.** One minus the probability of a normal ear is
   compared with a threshold chosen on the validation split to refer 97% of
   abnormal ears.
6. **Explanation.** Because the network ends in average pooling and one linear
   layer, the class activation map comes from the same forward pass as the
   prediction, with no approximation.

## Data and validation

4,763 images in 16 folders from three public datasets were merged into 12
classes: `Ear wax` with `Cerumen_Impaction`, `tympanoskleros` with
`Myringosclerosis`, and three folders of fewer than 20 images each into
*Other Findings*. Two safeguards make the reported figures trustworthy:

- **No ear in both training and test.** One source is built from video, so the
  same ear appears in long runs of frames. Images were grouped by deep feature
  similarity and whole groups were assigned to one split. An earlier grouping
  by image hash left half of that source's test images with a near twin in
  training and overstated its accuracy; it was replaced.
- **Settings frozen before testing.** Model selection, calibration and the
  referral threshold all used the validation split. The test split was scored
  once, at the end.

## Known limitations

- **Retrospective, public data.** Performance on this clinic's otoscopes is
  unknown until it is measured on local images. That is the most important
  next step before any clinical use.
- **Performance depends on the source.** Recall is highest on the largest
  source dataset. The results for the other two sources are the better guide
  to performance on unfamiliar equipment.
- **Single source classes.** Eight classes come from one source each, so their
  scores may still reflect acquisition style. Chronic Otitis Media
  (unspecified) scored perfectly and should not be quoted as diagnostic skill.
- **Small classes.** Other Findings, Otitis Externa and some chronic subtypes
  have under 20 test images; their figures are imprecise.
- **One image, one ear.** Clinical assessment draws on history, both ears and
  audiometry. This system sees a single image.

## Changing the referral policy

The referral threshold trades missed disease against unnecessary referrals.
Measured on the test split, with each threshold chosen on validation:

| Target sensitivity | Test sensitivity | Test specificity | Missed of 501 | Unnecessary referrals of 217 |
| --- | --- | --- | --- | --- |
| 90% | 90.0% | 87.1% | 50 | 28 |
| 95% | 95.0% | 78.3% | 25 | 47 |
| **97% (default)** | **97.0%** | **75.6%** | **15** | **53** |
| 99% | 99.0% | 72.8% | 5 | 59 |

To change it, run `python scripts/calibrate.py --target-sensitivity 0.99` (or
another value) and restart. The report regenerates automatically. Which
conditions should trigger referral at all, for example whether uncomplicated
cerumen should be managed locally, is a clinical decision for the supervising
ENT surgeon.

## Retraining with new data

Add or correct images in the `Dataset/` folders (one folder per condition;
update `TAXONOMY` in `scripts/prepare_dataset.py` for any new folder), then:

```bash
python scripts/prepare_dataset.py   # normalise, group by ear, split
python scripts/train.py             # about 40 minutes on a 4 core CPU
python scripts/calibrate.py         # calibrate, test once, write the report
python -m pytest                    # 23 checks, including privacy and orientation
```

The class order is written to `artifacts/labels.json` and travels with the
weights, so a retrained model can never silently mislabel its outputs.

## Privacy

Uploaded images are read into memory, analysed and discarded. Nothing derived
from a patient image is written to disk, uploaded filenames are never logged
(clinic filenames often contain patient names), and responses are marked
`no-store` so browsers do not cache them. A test in the suite fails if any file
is created during an analysis. By default the service only accepts connections
from the same computer.

## Deployment

`start.bat` serves the interface on the local machine only. To make it
available to other computers on a clinic network, run
`python app.py --host 0.0.0.0` and place it behind a reverse proxy that
provides HTTPS and access control. On Linux, `uwsgi.ini` and `Procfile`
configure uWSGI.

### API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/v1/analyse` | Multipart form with a `file` field. Returns the referral, finding, differential, chronic rollup, image quality warnings and attention images. |
| `GET` | `/api/v1/model` | Model version, referral threshold, test results and the class list. |
| `GET` | `/api/v1/health` | Service status. |

## Project structure

```
app.py                     Entry point; serves with Waitress
setup.bat, start.bat       One click setup and launch on Windows
src/ear_detection/
    imaging.py             Field of view normalisation, shared with training
    model.py               Inference, calibration, attention maps
    api.py                 HTTP API and static file serving
    config.py              Paths, class names and descriptions
frontend/                  Web interface and the generated report page
models/ear_classifier.keras
artifacts/                 Label order, split manifest, calibration, evaluation
scripts/                   prepare_dataset, train, calibrate, report, export_samples
tests/                     Imaging, API and report tests
docs/VALIDATION_REPORT.md  Generated validation report
notebooks/                 Experiments from the original prototype (historical)
```

## References

- Sandler M. et al. MobileNetV2: Inverted Residuals and Linear Bottlenecks. https://arxiv.org/abs/1801.04381
- Guo C. et al. On Calibration of Modern Neural Networks. https://arxiv.org/abs/1706.04599
- Zhou B. et al. Learning Deep Features for Discriminative Localization. https://arxiv.org/abs/1512.04150
- https://www.sciencedirect.com/science/article/pii/S2352396419304311

---

Nawaloka Hospital PLC, Research and Education Foundation
