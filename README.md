---
title: PomPom
emoji: 👀
colorFrom: yellow
colorTo: pink
sdk: static
app_file: index.html
pinned: false
short_description: A dancing focus buddy for Reachy Mini Wireless
tags:
  - reachy_mini
  - reachy_mini_python_app
thumbnail: https://huggingface.co/spaces/tinjyuu/pompom/resolve/main/pompom/static/icon.png
---

<p align="center"><img src="https://huggingface.co/spaces/tinjyuu/pompom/resolve/main/pompom/static/icon.png" width="160" height="160" alt="PomPom mascot — a cheerful dancing robot"></p>

**English | [日本語](README.ja.md)**

# PomPom

A little focus companion for Reachy Mini Wireless. PomPom dances when you stay off your phone, reminds you when you pick it up, and celebrates when you put it down.

The app runs on the robot and connects directly to OpenAI Decisions API. No always-on Mac or shared backend is required. **The robot control screen and recorded voice reminders are currently in Japanese.**

## Get started

1. Install [this Space](https://huggingface.co/spaces/tinjyuu/pompom) from your Reachy Mini dashboard, then launch `pompom`.
2. Open **http://reachy-mini.local:8043/**, or use your robot's IP address with port `8043`.
3. Enter your own OpenAI API key. Check the camera view and test a notification.
4. Select one or more of **Phone**, **Slouching**, and **Custom**. For Custom, describe a condition that can be seen by the camera.
5. Save your focus duration (25 minutes by default), consent to sending camera images, and start.

The Space is an introduction and distribution page. Enter your API key on the robot's control screen. OpenAI API usage charges and data retention terms apply.

## Choose conditions

Select multiple conditions to monitor together. PomPom reacts when any selected condition persists for its required time. Each condition has its own visibility check and timer; all selected conditions share a single image request. The default is **Phone**:

- **Phone:** react to a smartphone held in a hand; a phone resting on the desk is excluded. Requires two seconds of confident detection.
- **Slouching:** react to a visibly rounded upper back with the head or shoulders leaning forward. Requires three seconds. Position the camera so the head, shoulders, and torso are visible; a close-up of the face is insufficient.
- **Custom:** enter up to 1,000 characters describing a directly visible action or posture, for example “holding a book in front of my face.” Requires three seconds. Conditions requiring hidden information or an internal mental state are not suitable for image-only detection.

For all conditions, probability must be at least 0.8 and visibility at least 0.7. Unclear views are held as unknown. Changing the condition ends the current session and discards its pending results; start a new session after saving.

## Cumulative API usage

The control screen shows completed request counts, successes, failures, and input/output/total tokens since collection began. Token totals use the API's reported `usage`, not estimates; responses without token usage are counted separately. Results received after a session is stopped still count toward usage.

Counters persist across sessions and app restarts in `~/.config/pompom/usage.json` (mode `0600`). The file contains counters and a start timestamp, with no images, prompts, conditions, or API keys. These are local app totals from version 0.2.0 onward, not account-wide usage or billing amounts. A storage warning is displayed if totals cannot be persisted.

## How it behaves

- After 10 seconds of clear results where all selected conditions are absent, PomPom performs the official `dance1` motion.
- While dancing, the first returned phone-holding detection interrupts the dance and starts `fear1`.
- Otherwise, phone-holding probability of at least 0.8 and visibility of at least 0.7 must persist for two seconds to trigger a reminder. There is no cooldown.
- When Phone triggers a reaction, the preset emotion sound is followed by one of two randomly selected Japanese voice reminders. Slouching and Custom keep the preset motion and sound, without playing the phone-specific recording.
- Once a previously detected condition clears and all selected conditions are clearly absent, PomPom celebrates once with `cheerful1`.
- Pause, stop, settings changes, and timer expiry take effect locally; late inference results are discarded.
- Uncertain results, communication failures, or a detection gap of 10 seconds reset the focus streak and stop the dance.
- The timer and observation continue on the robot when the control page is closed, provided the app keeps running.

Response time depends on capture, network, and inference latency; two seconds is a detection threshold, not a response-time guarantee. A phone resting on a desk is excluded. Pause the session when you need your phone for work. PomPom does not diagnose concentration, intent, or medical conditions.

## Direct OpenAI connection and privacy

The app sends requests to `https://api.openai.com/v1/decisions` using `gpt-6-luna`, following the [Decisions API guide](https://developers.openai.com/api/docs/guides/decisions). Your API key must have access to this API and model. API errors appear on the control screen.

- Images are resized in memory to a maximum long edge of 640 pixels and a maximum JPEG size of 1 MiB. The app does not persist images.
- Only one inference request runs at a time, with at least one second between captures. Rate limits trigger a 30-second wait; quota, authentication, and permission errors pause the session.
- The API key is stored on the robot in `~/.config/pompom/settings.json` with mode `0600`. It is excluded from API responses to the control screen and from app logs.
- `OPENAI_API_KEY` can provide the initial key when no settings file exists yet.

## If the control screen does not open

The log line `ERROR - Using Python … environment at: /venvs/apps_venv` alone does not mean installation failed. Check the following lines for `Successfully installed` and `Job 'install' completed successfully`.

Launch PomPom in the dashboard, then open the robot's address on **port 8043**. Being able to reach the dashboard on port 8000 does not guarantee that port 8043 is reachable through the same network route. If you use Tailscale, try the robot's Tailscale address with `:8043`. A successful `/api/status` response confirms app initialization; it does not verify live OpenAI inference.

## Keep the original app

PomPom is separate from the shared-API [Reachy Focus app](https://github.com/aieo-product/devdayHackathon261018). It uses its own `pompom` package and entry point, port `8043`, and `~/.config/pompom/` settings directory.

It does not remove or overwrite `reachy_focus`, its port `8042`, or its `~/.config/reachy-focus/` settings. Choose which app to run in the robot's app manager.

## Development and validation

Git clones require Git LFS. Binary assets are served from the public Hugging Face Space. Installing through the Reachy dashboard uses the SDK to download these assets.

```sh
python -m pip install -e '.[dev]'
python -m pytest -q
ruff check pompom tests
python -m build
python scripts/check-package.py
reachy-mini-app-assistant check .
```

On Linux, the SDK build requires Cairo and GObject development libraries; see the CI workflow for installation steps.

PomPom inherits motions and voice assets tested on the original shared-API app. Automated tests cover the direct API client, inference failures, late replies after stopping, timers, and audio headers. At the initial release, paid live OpenAI requests, real-image classification through PomPom, and long-running hardware operation had not been verified. Publishing a Space does not automatically list it in the standard Reachy app catalog.
