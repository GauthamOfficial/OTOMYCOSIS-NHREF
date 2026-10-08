# Validation Report

Ear condition screening from otoscope images. Nawaloka Hospital Research
and Education Foundation (NHREF).

Generated 2026-10-08 17:43:10 by `scripts/calibrate.py` from the model in `models/`. Every
figure below was measured, none was written by hand. Regenerate this file
after any retraining.

## Summary

| Measure | Result | 95% CI |
| --- | --- | --- |
| Referral sensitivity (abnormal ears referred) | **97.0%** | 95.1 to 98.2% |
| Referral specificity (normal ears not referred) | **75.6%** | 69.4 to 80.8% |
| AUROC, abnormal vs normal | 0.974 | |
| 12 class macro F1 | 0.686 | |
| 12 class balanced accuracy | 68.3% | |
| Coarse accuracy, chronic subtypes merged | 86.2% | |

Test split: 718 images never used for training, model selection or
calibration. Out of 501 abnormal test ears, 15 were not referred.

## Intended use

A screening aid for deciding whether an otoscope image warrants review by an
ENT specialist. It is designed to err toward referral: when the model cannot
confidently call an ear normal, it recommends referral.

It is **not** a diagnostic device. It has not been prospectively validated,
has not been evaluated on images from the deploying clinic's own equipment,
and must not be used to withhold care. Every output requires review by a
qualified clinician.

## How the referral decision works

The model outputs a probability for each of 12 conditions. The probability of
an abnormal ear is one minus the probability of `normal`. The ear is referred
when that exceeds a threshold of **0.2320**, chosen on the validation split to
reach a target sensitivity of 97.0%, then frozen before the test split was scored.

Other operating points, threshold chosen on validation, measured on test:

| Target sensitivity | Threshold | Test sensitivity | Test specificity |
| --- | --- | --- | --- |
| 90.0% | 0.4849 | 90.0% | 87.1% |
| 93.0% | 0.3812 | 93.4% | 83.4% |
| 95.0% | 0.2857 | 95.0% | 78.3% |
| 97.0% | 0.2320 | 97.0% | 75.6% |
| 99.0% | 0.1891 | 99.0% | 72.8% |

Abnormal test ears that were **not** referred at the chosen threshold:

| Condition | Missed |
| --- | --- |
| Myringosclerosis | 11 |
| Acute Otitis Media | 2 |
| Cerumen Impaction | 1 |
| Other Findings | 1 |

## Per condition performance

Recall is the fraction of ears with that condition the model named correctly.
Classes with a small test support have wide intervals; read them accordingly.

| Condition | Test images | Precision | Recall | Recall 95% CI | F1 |
| --- | --- | --- | --- | --- | --- |
| Acute Otitis Media | 123 | 0.836 | 0.870 | 79.9 to 91.8% | 0.853 |
| Cerumen Impaction | 112 | 0.982 | 0.955 | 90.0 to 98.1% | 0.968 |
| Chronic OM, Active, No Cholesteatoma | 13 | 0.667 | 0.923 | 66.7 to 98.6% | 0.774 |
| Chronic OM with Cholesteatoma | 9 | 0.700 | 0.778 | 45.3 to 93.7% | 0.737 |
| Chronic OM, Inactive | 16 | 0.818 | 0.562 | 33.2 to 76.9% | 0.667 |
| Chronic Suppurative OM | 10 | 0.400 | 0.200 | 5.7 to 51.0% | 0.267 |
| Chronic Otitis Media | 91 | 1.000 | 1.000 | 95.9 to 100.0% | 1.000 |
| Myringosclerosis | 95 | 1.000 | 0.632 | 53.1 to 72.2% | 0.774 |
| Normal Ear | 217 | 0.774 | 0.899 | 85.1 to 93.2% | 0.832 |
| Other Findings | 5 | 0.400 | 0.400 | 11.8 to 76.9% | 0.400 |
| Otitis Externa | 6 | 0.500 | 0.167 | 3.0 to 56.4% | 0.250 |
| Otitis Media with Effusion | 21 | 0.630 | 0.810 | 60.0 to 92.3% | 0.708 |

## Confusion matrix

Rows are the true condition, columns the prediction, in this order:

0. Acute Otitis Media
1. Cerumen Impaction
2. Chronic OM, Active, No Cholesteatoma
3. Chronic OM with Cholesteatoma
4. Chronic OM, Inactive
5. Chronic Suppurative OM
6. Chronic Otitis Media
7. Myringosclerosis
8. Normal Ear
9. Other Findings
10. Otitis Externa
11. Otitis Media with Effusion

```
          0    1    2    3    4    5    6    7    8    9   10   11
   0    107    .    .    2    .    1    .    .   12    .    .    1
   1      2  107    .    .    .    .    .    .    3    .    .    .
   2      .    .   12    .    1    .    .    .    .    .    .    .
   3      1    .    .    7    1    .    .    .    .    .    .    .
   4      .    .    5    1    9    .    .    .    .    .    .    1
   5      .    .    1    .    .    2    .    .    5    2    .    .
   6      .    .    .    .    .    .   91    .    .    .    .    .
   7      .    .    .    .    .    1    .   60   33    1    .    .
   8     13    1    .    .    .    .    .    .  195    .    .    8
   9      .    .    .    .    .    1    .    .    1    2    1    .
  10      2    1    .    .    .    .    .    .    2    .    1    .
  11      3    .    .    .    .    .    .    .    1    .    .   17
```

## Calibration

Probabilities are temperature scaled (T = 1.20, fitted on validation) so that a
stated confidence corresponds to an observed frequency.

| | Before | After |
| --- | --- | --- |
| Expected calibration error, test | 0.050 | 0.043 |
| Negative log likelihood, test | 0.391 | 0.380 |

When the two most likely conditions are close, the finding is marked low
confidence. That applied to 4.0% of test images. Accuracy was 87.2% on the
confident ones and 31.0% on the flagged ones, so the flag separates reliable
findings from unreliable ones.

## Dataset and leakage controls

4,763 images in 16 source folders, drawn from three public datasets, merged into
12 classes. Two controls matter for reading the numbers above:

1. **Same ear grouping.** One source dataset is built from video, so the same
   ear appears in long runs of frames. Images were grouped by the cosine
   similarity of generic ImageNet features (0.93 or above, joined
   transitively) and whole groups assigned to a single split. No test image has
   a training image of the same class at or above that similarity. An earlier
   split grouped by perceptual hash only; it left half of that source's test
   images with a near twin in training and inflated its scores. The figures in
   this report use the corrected split.
2. **Field of view normalisation.** The three source datasets frame the ear
   differently, and 8 of the 12 classes come from a single source. Without
   correction the network can identify the dataset instead of the ear. Every
   image is cropped to its illuminated field and masked to one fixed circle, at
   training and at inference, which removes the framing cue.

Recall by source, for conditions present in more than one source. Similar
figures across sources indicate the model is reading the ear rather than
the dataset:

| Condition | Source A | Source B | Source C |
| --- | --- | --- | --- |
| Acute Otitis Media | 85/89 (95.5%) | 9/18 (50.0%) | 13/16 (81.2%) |
| Cerumen Impaction | 90/90 (100.0%) | 17/22 (77.3%) |  |
| Myringosclerosis | 58/91 (63.7%) | 2/4 (50.0%) |  |
| Normal Ear | 88/88 (100.0%) | 67/82 (81.7%) | 40/47 (85.1%) |

## Known limitations

- **Perfect scores to distrust.** Chronic Otitis Media scored perfectly on the test split.
  Each comes from a single source dataset, so perfection more likely reflects
  that source's acquisition style than clinical skill. Do not quote these
  figures as diagnostic performance.
- **Performance depends on image source.** Where a condition appears in several
  sources, recall is highest on source A, the largest. Results on sources B and
  C in the table above are the better guide to performance on unfamiliar
  equipment.
- **Single source classes.** Eight classes come from one source dataset each.
  Field of view normalisation removes the framing cue, but differences in
  camera, compression and image quality remain, so scores for those classes
  may still be optimistic. A prospective test on local equipment is the only
  way to confirm them.
- **Small classes.** Other Findings, Otitis Externa and the cholesteatoma and
  suppurative subtypes have few test images. Their intervals above are wide.
- **Pooled class.** Other Findings combines foreign bodies, pseudomembranes and
  ventilation tubes, which had too few images to learn separately.
- **Otomycosis is not a class.** The dataset contains no otomycosis folder, so
  fungal infection cannot be recognised as such.
- **Chronic subtypes overlap.** Chronic Otitis Media (unspecified) is a parent
  of the other chronic labels in the source data. The coarse accuracy above
  merges them; subtype calls should be read with that in mind.
- **Single image.** Clinical assessment uses history, both ears and often
  audiometry. This system sees one image.

