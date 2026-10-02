**DRAFT: this text hasn't had a legal review yet. Contributions aren't open until it has.**

# Recording your hands for the Frametop hand dataset

Version: 2026-10-02

Frametop's hand tracking needs a small model that finds hands in the headset's camera images. To train it, we're collecting recordings of many people's hands. This page explains what the hand recorder records, what happens to it, and what you agree to if you take part. Please read all of it.

## Who can take part

You must be 18 or older.

## What is recorded

While a session runs, the hand recorder saves:

- **Camera images.** Infrared images from the headset's 4 tracking cameras, about 10 sets a second. They show your hands, your arms and whatever is in front of you: your room, your desk and the things on it. They're grey, low-detail images, but people and things can be recognised in them.
- **Motion.** The position and rotation of the headset and the controllers, many times a second.
- **Prompts.** What you were asked to do and when, and what the live hand tracker saw at the time.
- **Calibration.** Where the cameras sit on the headset and how their lenses bend the image. Serial numbers and other fields that identify your headset are removed first, and the export lists what was removed.
- **Your checklist answers.** Which objects you had, the lighting you chose, whether you wore sleeves, rings or a watch, and any notes you typed in.
- **A contributor id.** A random number made on your headset the first time you agree to this page. It isn't linked to your name, your Steam account or your headset. It lets us keep your sessions together and find them if you withdraw.

The recorder doesn't record sound, your name, your email address or your account. Your eyes and face aren't recorded: the tracking cameras look outward.

## Nothing leaves your headset unless you send it

- Recordings stay on your headset, in `~/.local/share/frametop/hands/contrib`. Nothing is uploaded automatically.
- Before you share anything, you can watch every recording in the Review page. You can delete any stretch of a recording, a whole take or a whole session.
- Export makes a package from what you kept. You upload it yourself, with your own Hugging Face account, following the Upload page. Until you do, nobody else has it.
- An upload opens a pull request. The maintainer checks it before it becomes part of the dataset, and may decline it.

## Keep other people and private things out of view

While recording, please:

- face away from other people, mirrors, screens showing private things, papers, letters and anything else you wouldn't want in a public dataset;
- make sure nobody else's face or hands are in view.

If something private got into a recording, delete that stretch in Review before you export. If you notice it after uploading, withdraw the session (below).

## The license

- **The dataset is published under Creative Commons Attribution-NonCommercial 4.0 (CC BY-NC 4.0).** Anyone may use it for non-commercial purposes, with attribution. Your contribution is credited by its contributor id, not your name.
- **You also give the maintainer, DeeJanuz, a non-exclusive license to use your contribution for any purpose, including commercially.** That includes copying it, changing it, and training, publishing and selling models made from it, in Frametop and elsewhere. It's non-exclusive: you keep any rights you have in your recordings and can do what you like with your own copies.
- You confirm that you have the right to give these licenses: the recordings are yours, and nothing in them belongs to someone who hasn't agreed.
- There is no payment, and the dataset comes with no warranty.

## Withdrawing

You can withdraw a contribution at any time. Send your contributor id (shown in the hand recorder) and which sessions to withdraw, or "all", through the dataset's discussion page or the Frametop repository's issues. Then:

- your recordings are deleted from the dataset and purged from its repository's history, so they can't be downloaded from there again;
- they're left out of anything trained after that.

What can't be undone: copies others downloaded before the withdrawal, and models already trained with them.

## Agreeing

By ticking "I'm 18 or older" and "I agree", you confirm the above. You can still decide not to upload anything. If this text changes, the hand recorder asks you again before your next session.
