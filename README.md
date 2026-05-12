# Matrix–SIP voice bridge

Master’s thesis project (FIT BUT): a **voice call bridge** between the **Matrix** protocol (native Element/WebRTC calls via `m.call.*`) and **SIP** (RTP, typically through **Asterisk** or a **SIP provider** via **pyVoIP**). The goal is bidirectional calling: SIP users can reach Matrix users and vice versa, with audio transcoded where needed (e.g. PCMU/μ-law ↔ Opus/WebRTC).

## What is in this repository

| Flow | Module | Role |
|------|--------|------|
| **SIP → Matrix** | `sip_bridge` | Asterisk receives SIP (e.g. Linphone), Stasis/ARI drives **External Media** RTP to this app; a **Matrix bot** sends `m.call.invite` and completes WebRTC with **MediaBridge** (aiortc). |
| **Matrix → SIP** | `matrix_bridge` | The same bot pairs 1:1 with your Matrix user; native Matrix voice calls are bridged over **WebRTC** to **outbound SIP** using **pyVoIP** and MediaBridge. |

Supporting pieces:

- **`MediaBridge.py`** — RTP (Opus or PCMU) ↔ PCM ↔ WebRTC (Opus) relay at 48 kHz.
- **`bot/MatrixBot.py`** — Matrix client (matrix-nio): login, rooms, `m.call.*` signalling.
- **`config/asterisk/`** — sample Asterisk/PJSIP/ARI/dialplan snippets used with the provided **`scripts/run_asterisk.sh`** Podman setup.
- **`register.yaml`** — maps Asterisk dialled extension (string) → Matrix user ID (`@user:server`).

## Requirements

- **Python 3.12+** and **[Poetry](https://python-poetry.org/)**
- For the default SIP→Matrix path: **[Podman](https://podman.io/)** (or adapt the script for Docker) to run Asterisk with the bundled config
- A **Matrix** account for the bot and test users; optionally **Linphone** (or any SIP phone) against Asterisk

## Setup

1. **Install dependencies** (from the repository root):

   ```bash
   poetry install
   ```

2. **Environment** — copy the example and edit secrets and hosts:

   ```bash
   cp bot/.env.example bot/.env
   ```

   See comments inside `bot/.env.example`. The code loads `bot/.env` via `python-dotenv` when you run the bridge modules.

3. **Extension → Matrix user** — edit `register.yaml` so each dialled extension points to the intended Matrix ID.

## How to run

### SIP → Matrix (Asterisk + `sip_bridge`)

1. Start Asterisk with the repo config (host network, ARI on **8088** by default):

   ```bash
   ./scripts/run_asterisk.sh
   ```

2. In another terminal, from the repo root:

   ```bash
   poetry run python -m sip_bridge
   ```

   Ensure `bot/.env` contains Matrix bot credentials, ARI URL/user/password, Stasis app name, and (for caller display) the Linphone SIP domain/user/password used in Asterisk. Dial an extension listed in `register.yaml` from your SIP client; the mapped Matrix user should receive a native voice call.

### Matrix → SIP (`matrix_bridge`)

From the repo root (Asterisk **not** required for this path; you need a reachable **SIP provider** and `REVERSE_PYVOIP_*` settings in `bot/.env`):

```bash
poetry run python -m matrix_bridge
```

Set `MY_MATRIX_USERNAME` to your MatrixID. The bot accepts pairing into a **1:1** room, then you can place Matrix voice calls that are bridged to SIP per the reverse-bridge logic.

### Optional checks

- Matrix bot env / login smoke test:

  ```bash
  poetry run python scripts/check_matrix_bot.py
  ```

## Tests

```bash
poetry run pytest
```

## Configuration reference

- **`bot/.env.example`** — documented variables for `bot/.env`.
- **`config/asterisk/`** — must stay consistent with `ASTERISK_*` variables in `bot/.env`.

## License

See [`LICENSE`](LICENSE) (MIT).

## Author

Štefan Gajdošík — [xgajdo30@stud.fit.vut.cz](mailto:xgajdo30@stud.fit.vut.cz)
