# Pose images for the hand recorder

Small pictures of each hand pose in `script.json`, shown on the headset panel next to the prompt text.

- `<id>.png`: one image per pose, 512x512 RGBA with a transparent background. Made for a dark panel and still readable at about 250 px.
- `poses.json`: maps each pose id to `{"file", "two_hands", "caption"}`.
- `contact-sheet.png`: every image in a grid with its id, for review. The panel doesn't use it.
- `make_poses.py`: the generator that makes all of the above.

## How the images are drawn

Every image shows a right hand as the wearer sees it from their own eyes. "Palm toward you" shows the palm, with its creases. "Back toward you" shows the back of the hand, with the nails and knuckles. Fingers point up unless the pose says otherwise.

- **Left-hand prompts:** the panel mirrors the image horizontally.
- **"Both" prompts:** the panel shows two copies, one of them mirrored.
- **`two_hands: true`:** the image already shows the whole scene: both hands, or one hand with an object, as in `hold`. Show it once, without mirroring or copying. This applies to `cross`, `overlap`, `near-face`, `typing`, `lift`, `switch`, `hold`, `push`, `push-controller`, `touch-stick` and `touch-stick-desk`.
- **`touch-stick`:** shows the left hand holding the controller while the right index touches its thumbstick. For prompts where the right hand holds the controller (`controllers: ["right"]`), mirror it.

`push` and `push-controller` are side views: the wearer's head with a headset on, one arm out in front with the palm out, a ghost of the hand further out, and a straight double arrow from the headset outward, labelled "near" and "arm out". The labels use Pillow's built-in font.

Other motion poses show the start pose, a faint blue "ghost" of the end pose, and orange arrows. Objects (keyboard, mouse, bottle, bar, controller, screen, head and headset) are plain grey shapes.

Besides the pose ids in `script.json`, these extra ids exist for prompts that need a different picture:

| id | for |
| --- | --- |
| `push` | the bar sections: push straight out from the headset and back, palms out |
| `push-controller` | the same with controllers on |
| `touch-stick-desk` | touching the thumbstick of a controller lying on the desk |
| `no-hands` | the no-hands section: hands down, out of view |
| `open-close` | already used by the bar sections |

`count-5` is the same picture as `spread`.

## Regenerating

You need Python 3 with numpy and Pillow. The script uses about one core-minute per image at the default quality, so don't run it on the headset. Run it on a build machine:

```sh
python3 -m venv venv && venv/bin/pip install numpy pillow
venv/bin/python make_poses.py --jobs 6              # all images, poses.json, contact sheet
venv/bin/python make_poses.py --only fist,ok        # just some (poses.json is left alone)
venv/bin/python make_poses.py --ss 1 --jobs 6       # rough and about 8x faster, for trying things
```

`--out DIR` writes somewhere other than this folder.

Each pose is a short entry in the `POSES` table in `make_poses.py`: a caption, the `two_hands` flag, and a function that returns the scene. A scene is the camera, layers of hands and objects (a layer can be a faint ghost), and arrows. Hands are built from joint angles: flexion at each finger's three joints, sideways spread, and four thumb angles. A thumb can also be given a target point, such as "touch the index fingertip", and a small solver finds the angles. The presets near `FLAT`, `FIST` and `OK` are a good place to start a new pose.

## License

MIT, like the rest of the repository. The script draws everything itself. A hand made of a palm slab and tapered capsules is ray-marched as a signed distance field, then shaded and outlined. No photos, downloaded images, scanned or research hand models, or AI image generators are involved, so the images carry no other terms.
