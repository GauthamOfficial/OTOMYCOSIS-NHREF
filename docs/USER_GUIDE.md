# User Guide

Ear Disease Detection System. Nawaloka Hospital PLC, Research and Education
Foundation.

> This is a screening aid for research use. It does not diagnose, and every
> result must be reviewed by a qualified ENT practitioner.

## Signing in

Sign in with the username and password your administrator gave you. The first
time, you must replace the temporary password with your own: at least 10
characters, mixing letters with numbers or symbols.

For security you are signed out after 30 minutes without activity, and after
five wrong passwords the account locks for 15 minutes.

## Screening a patient

1. **Patient.** Enter the hospital record number, age, sex and which ear was
   examined. For a returning patient, the system recognises the record number
   and fills in the sex. Names are never entered or stored.
2. **Image.** Drop the otoscope image onto the page or click to select it.
3. **Run Analysis.** The result appears in a few seconds and is saved to the
   patient's record.
4. **Review.** Confirm or correct the finding (see below).

To start the next patient, click **Start next examination** or **Screening**.

## Reading the result

| Part | What it tells you |
| --- | --- |
| **Screening recommendation** | *Refer for ENT review* or *No abnormality detected*. The bar shows the probability that the ear is abnormal against the referral threshold. |
| **Most likely finding** | The leading condition and its probability. *Low confidence* means two conditions were close, so the differential deserves more weight. |
| **Chronic otitis media, any subtype** | The combined probability of all chronic subtypes. More reliable than any single subtype. |
| **Differential** | Every condition with its probability, most likely first. |
| **Model attention** | Where in the image the evidence came from. Tinting on the tympanic membrane or canal is expected. Tinting on glare, the image edge or unrelated debris is a reason to distrust the result. |

The system leans toward referral on purpose: if it cannot confidently call an
ear normal, it recommends referral and says so.

## Reviewing a finding

Your review is the most valuable thing in the system. It measures how well
the model performs on our own patients and equipment, and it is what a future
model learns from.

- **Confirm.** If the finding is right, click the green **Confirm** button.
- **Correct.** If it is wrong, choose the actual condition from the list and
  click **Save review**. The model's prediction is always first in the list.
- **Other diagnosis.** If the condition is not in the list, type it into the
  *Other diagnosis* box. These are tracked separately and can become new
  categories once enough examples are collected.

Add a comment if anything is worth recording. You can change a review later;
every earlier verdict is kept, with who made it and when.

Please review every examination, including the ones the model got right.
Confirmations matter as much as corrections.

## History

**History** lists every examination. Search by record number, or show only
those awaiting review, those you disagreed with, or those referred. Click a
row to open the full result and the original image.

## Dashboard

**Dashboard** shows how the model performs on reviewed cases from this clinic,
with your verdicts as the reference. It includes agreement with the surgeons,
referral sensitivity and specificity with confidence intervals, results per
condition, the most common corrections and any other diagnoses entered.

These figures are only reliable once many cases have been reviewed; the page
warns you while the numbers are still small.

## Printing

**Print result** produces a one page summary with the recommendation first,
for the patient's notes.

## For administrators

**Admin** has four tabs.

### Users

Add an account for each person who uses the system, with a temporary password
given to them in person. You can change roles, deactivate accounts (which
signs the person out immediately) and reset passwords. You cannot deactivate
yourself or remove the last administrator.

### Model and retraining

When enough reviewed cases exist (50 by default), **Start retraining** trains
a candidate model on the original data plus the reviewed cases. It runs in the
background for about 40 minutes; the current model stays in service.

About 30 percent of patients are held back from training and used, with the
original frozen test set, to compare the candidate against the current model.
The comparison is shown side by side. A candidate that is worse on either test
fails the validation gate and cannot be approved.

A candidate that passes is still not used until an administrator clicks
**Approve and put live**. Approving switches every user to the new model at
once, without a restart, and regenerates the validation report. Each
examination keeps a record of which model version produced it.

Diagnoses entered as *Other* are not used for training, since there is no
category to learn them as. Adding a new category, such as otomycosis, needs
enough images and a full retraining from the dataset.

### Records

Shows how many patients, examinations, reviews and stored images exist.

To remove **one examination** recorded in error, for example against the
wrong record number, open it from History and click **Delete this
examination**. A reason is required. The examination, its reviews and its
images are deleted, and so is the patient entry if it has no other
examinations.

**Clear all records** permanently deletes every patient, examination, review
and stored image. It is meant for removing test data before the system is
used for real. Type the confirmation phrase exactly and enter your own
password to enable it. User accounts, model versions and the audit log are
kept.

Both cannot be undone, and reviewed cases are what retraining learns from.
Patient records may also fall under the hospital's retention rules, so do not
delete real records without approval. Every deletion, with its reason, is
recorded in the audit log.

### Audit log

Every sign in, failed sign in, examination, review, account change, retraining
run and model approval is recorded with the user, time and network address.
