**DRAFT: contributions aren't open yet. Please don't upload until this banner is gone.**

# Uploading your recordings

Your export is ready:

- Folder: `@EXPORT_PATH@`
- Size: @EXPORT_SIZE@
- Contributor id: `@CONTRIBUTOR@`

Uploads go to the Hugging Face dataset [@DATASET@](https://huggingface.co/datasets/@DATASET@), from your own Hugging Face account. Nothing is uploaded until you press Upload (or run the command below). The upload opens a pull request, so nothing is published until the maintainer has looked at it.

You can do every step below in the headset: the web pages in a browser window, the login in a terminal window. Once your pull request is open, you plug the headset in and leave it to finish.

## 1. Make a Hugging Face account

Sign up at <https://huggingface.co/join>, if you don't have an account. Your username shows on your pull request, but the dataset credits your contributor id, not your name.

## 2. Accept the dataset's terms

Open <https://huggingface.co/datasets/@DATASET@>, read the terms and accept them. They're the same as the consent you agreed to in the hand recorder. Until you've accepted them, the upload stops with "accept the dataset's terms first".

## 3. Create a write token

Go to <https://huggingface.co/settings/tokens>, press "Create new token", choose "Write" and give it a name like "frametop-hands". Copy the token.

The token lets anyone who has it change things in your account. Paste it only into your own terminal in the next step: never into a chat, a website, an issue or this window. The hand recorder never asks for it.

## 4. Log in, in a terminal

Open a terminal (Konsole) and run:

```
@LOGIN@
```

It asks for the token: paste it there (it doesn't show as you paste) and press Enter. The token is saved in your home folder, in `~/.cache/huggingface/token`, where the hand recorder's upload finds it. Then press "Check again" on this page: it should say you're logged in.

If the page says `huggingface_hub` isn't installed, update the dev container first: run `setup/dev-container.sh` from the Frametop folder.

## 5. Upload

Press **Upload** on this page. It first checks the export (that every file is complete and matches its checksum, and that nothing identifying is left in). Then it opens your pull request and shows its link, and starts uploading the files to it, into `contributions/@CONTRIBUTOR@/@SESSION@` in the dataset.

**Once the link shows, plug in the headset and leave it plugged in until the page says Uploaded.** A round is several gigabytes, so this can take a while. You can take the headset off: the Hand Recorder keeps it awake until the upload is done. Keep the Hand Recorder open, since closing it stops the upload.

If it stops partway (Cancel, or the network drops), press Upload again: it carries on in the same pull request, and files already sent aren't sent twice.

### Or upload from a terminal

If the Upload button doesn't work for you, run this in the dev container (`distrobox enter dev`) after step 4. It checks the export the same way, then uploads it:

```
@COMMAND@
```

## 6. The maintainer reviews it

The maintainer checks your pull request (that the files are complete, and that nobody else and nothing private is in view) before merging it into the dataset. You can follow it, and answer questions, on the pull request's page.

When it's merged, you can delete the session and its export on your headset to free the space.

To withdraw a contribution later, see "Withdrawing" in the consent text: you'll need your contributor id, `@CONTRIBUTOR@`.
