**DRAFT: contributions aren't open yet. Please don't upload until this banner is gone.**

# Uploading your recordings

Your export is ready:

- Folder: `@EXPORT_PATH@`
- Size: @EXPORT_SIZE@
- Contributor id: `@CONTRIBUTOR@`

Uploads go to the Hugging Face dataset [@DATASET@](https://huggingface.co/datasets/@DATASET@). You do it yourself, from your own account; the hand recorder never uploads anything. The upload opens a pull request, so nothing is published until the maintainer has looked at it.

## 1. Make a Hugging Face account

Sign up at <https://huggingface.co/join>, if you don't have an account. Your username shows on your pull request, but the dataset credits your contributor id, not your name.

## 2. Accept the dataset's terms

Open <https://huggingface.co/datasets/@DATASET@>, read the terms and accept them. They're the same as the consent you agreed to in the hand recorder.

## 3. Create a write token

Go to <https://huggingface.co/settings/tokens>, press "Create new token", choose "Write" and give it a name like "frametop-hands". Copy the token.

The token lets anyone who has it change things in your account. Paste it only into your own terminal in the next step: never into a chat, a website, an issue or this window.

## 4. Install the Hugging Face tools

Open a terminal (Konsole) and enter the dev container, then install `huggingface_hub` and log in:

```
distrobox enter dev
pip install --user huggingface_hub
huggingface-cli login
```

`huggingface-cli login` asks for the token: paste it there. If it asks whether to add the token as a git credential, answer no.

## 5. Upload

In the same terminal, run:

```
@COMMAND@
```

It uploads the export folder to `contributions/@CONTRIBUTOR@/@SESSION@` in the dataset and opens a pull request. Large uploads take a while. If it fails partway, run the same command again: parts already sent usually aren't sent twice. That can open a second pull request, which is fine: the maintainer closes the incomplete one.

Keep the headset on its charger or plugged in while it uploads.

## 6. The maintainer reviews it

The maintainer checks your pull request (that the files are complete, and that nobody else and nothing private is in view) before merging it into the dataset. You can follow it, and answer questions, on the pull request's page.

When it's merged, you can delete the session and its export on your headset to free the space.

To withdraw a contribution later, see "Withdrawing" in the consent text: you'll need your contributor id, `@CONTRIBUTOR@`.
