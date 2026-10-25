# কথন (Kothon) — Bangla Auto Caption

Video upload koro → auto Bangla caption → edit koro → style-soho caption burn kora MP4 download koro.
Reels / TikTok / Shorts creator der jonno. Shob kichu tomar Mac ei cholbe (internet lage shudhu prothombar model download er jonno).

**Kaj kore ja ja:**
- Bangla speech → text (Whisper, CPU te, tomar i9 e)
- Caption editor: text, start/end time, delete, notun caption add
- 3 ta style (Reels Pop / Classic / Box), 3 ta Bangla font, position, size, kotogulo word ekshathe
- Spoken word highlight (karaoke-style)
- "Frame preview": final video te hubohu ki dekhabe, render er age-i dekhte paro
- `.srt` download + burned-in `.mp4` download
- Watermark ("কথন দিয়ে তৈরি") on/off
- Shob kichu 24 ghonta pore auto delete

---

## 1. Setup (ekbar korlei hobe) — ~10 minute

> Requirement: macOS 11+, internet (prothombar), ~5 GB faka jayga.

**Step 1 — Folder ready koro**
Zip ta unzip kore folder ta `~/kothon` e rakho (naam e space rakhba na). Terminal kholo:
```bash
cd ~/kothon
```

**Step 2 — Setup script chalao**
```bash
chmod +x setup.sh run.sh
./setup.sh
```
Ei script nijei: Python 3.12 + ffmpeg install kore (Homebrew lage — na thakle script bole dibe), virtual environment banay, library install kore, tarpor `doctor` chalay.

**Step 3 — Doctor er result dekho**
Shob line e ✓ thakle thik. Tarpor ei file ta Preview diye kholo:
```bash
open data/doctor-sample.png
```
Bangla lekha (**বিশ্ব বাংলা ক্ষমতা**) jora-lagano, sundor dekhale ffmpeg ready. (Jodi juktakkhor bhenge jay, "Troubleshooting" dekho.)

**Step 4 — Model download koro (optional, kintu bhalo)**
```bash
.venv/bin/python scripts/download_model.py
```
~775 MB. Na korle prothom video te auto download hobe (tokhon UI te "Model load hocche" dekhabe).

**Step 5 — App chalao**
```bash
./run.sh
```
Browser e `http://127.0.0.1:8000` khule jabe. Bondho korte Terminal e `Ctrl+C`.

---

## 2. Prothombar use (10 minute er test)

1. Phone e **20–30 second** er ekta Bangla video nao (clear kotha, background music kom).
2. Upload koro → progress dekhbe: audio → model load → "Bangla kotha lekha hocche".
3. Caption list e vul thakle thik koro (auto-save hoy, "✓ সেভ হয়েছে" dekhabe). Row e click korle video oi jayga e jay.
4. Style bachho → **Frame preview** chapo (video pause kore je jayga e caption ache sekhane ne).
5. **ক্যাপশনসহ ভিডিও বানাও** → ready hole **ডাউনলোড**.
6. Phone e pathiye Facebook/TikTok e upload kore dekho text ta bhalo dekhay ki na.

---

## 3. ⚠ Shobcheye boro kaj: accuracy test (age, launch er age)

Bangla speech recognition er quality **nirbhor kore kon model, ar kon dhoroner kotha** tar upor. Ami tomar Mac e chalate parini, tai ei ta tomake-i verify korte hobe:

```bash
.venv/bin/python scripts/compare_models.py clip1.mp4 clip2.mp4 clip3.mp4
```
Ei script prottek video ta **tinta model** diye chalay, text + speed print kore. Tumi ja bolecho tar sathe milao.

| Model | Size | Kemon |
|---|---|---|
| `bn-medium` (default) | 0.8 GB | Bangla er jonno fine-tuned. Parishkar/pora-kotha te valo; casual/slang e kom hote pare |
| `large-v3` | 3 GB | Shobcheye general, slow. Casual kotha e valo hote pare |
| `small` | 0.5 GB | Dhrut, kintu bhul beshi |

10–15 ta real clip (tomar nijer, bondhu der, alada alada voice, kichu music soho) diye test koro. Je model jitbe seta default koro:
```bash
KOTHON_DEFAULT_MODEL=large-v3 ./run.sh
```
**Realistic expectation:** 100% hobe na. Tai-i caption editor ta product er asol feature — user 1–2 ta word thik kore nibe. Tomar goal: "edit korte 30 second", "shuru theke type korte 10 minute" na.

**Speed:** i9 e prothom video te measure koro (`compare_models.py` x-realtime dekhay). Jodi 60s video te 3+ minute lage, `small` model ba `KOTHON_MAX_SECONDS=30` rakho. Mac onek gorom hole: charger e rakho, `KOTHON_THREADS=6 ./run.sh`.

---

## 4. Settings (environment variable)

| Variable | Default | Mane |
|---|---|---|
| `KOTHON_MAX_SECONDS` | 60 | Maximum video length |
| `KOTHON_MAX_UPLOAD_MB` | 300 | Maximum file size |
| `KOTHON_THREADS` | 8 (ba CPU kom hole tar kom) | Whisper CPU thread |
| `KOTHON_DEFAULT_MODEL` | `bn-medium` | `bn-medium` / `large-v3` / `small` |
| `KOTHON_WATERMARK` | `কথন দিয়ে তৈরি` | Watermark text |
| `KOTHON_JOB_TTL_HOURS` | 24 | Koto ghonta pore video delete |
| `KOTHON_DATA_DIR` | `./data` | Upload/model jekhane thake |
| `KOTHON_PORT` / `KOTHON_HOST` | 8000 / 127.0.0.1 | Server address |

Example: `KOTHON_MAX_SECONDS=90 KOTHON_THREADS=6 ./run.sh`
Phone theke same Wi-Fi te try korte: `KOTHON_HOST=0.0.0.0 ./run.sh`, tarpor phone e `http://<Mac-er-IP>:8000` (shudhu nijer ghorer network e!).

---

## 5. Automated test

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest                    # 50+ test, ~20 second, model lage na
.venv/bin/python scripts/doctor.py  # environment check
```
Test gulo asol ffmpeg diye Bangla caption burn kore check kore (rotation, odd-size, no-audio, bhul file, boro file, restart, cleanup shoho). Browser test: `python tests/e2e_ui.py` (Playwright lage, optional).

---

## 6. Troubleshooting

| Somossa | Somadhan |
|---|---|
| `./setup.sh: Permission denied` | `chmod +x setup.sh run.sh` |
| `brew: command not found` | https://brew.sh theke Homebrew install kore abar chalao |
| Doctor: "libass nei" | `brew reinstall ffmpeg`. Na hole: `brew tap homebrew-ffmpeg/ffmpeg && brew install homebrew-ffmpeg/ffmpeg/ffmpeg` |
| Bangla juktakkhor bhanga / box box dekhay | `doctor-sample.png` dekho. ffmpeg er libass e HarfBuzz lage: `brew reinstall ffmpeg libass harfbuzz` |
| Prothom video e onek somoy "Model load" | Normal — 0.8–3 GB download hocche. Terminal e progress dekhte paro |
| "Model load/download hoyni" | Internet check. Abar try. `data/models` folder muche abar download |
| Port 8000 busy | `KOTHON_PORT=8001 ./run.sh` |
| Video preview browser e cholche na | Chrome/Safari use koro. (Final MP4 prottek player e cholbe) |
| Caption er timing ektu agge/pore | Row er start/end number thik koro, ba "একসাথে কয়টি শব্দ" kom/beshi koro |
| Mac onek gorom / fan | `KOTHON_THREADS=4`, charger e rakho, `small` model |
| Kichu bujhi na | Terminal er log copy kore amake dao |

---

## 7. Code structure

```
app/main.py         API (upload, status, transcript, preview, render, download)
app/transcribe.py   Whisper wrapper (model cache, word-timestamp fallback, repetition filter)
app/transcript.py   Segment/word timing logic, edit validation
app/subtitles.py    SRT + styled ASS generator (word highlight, chunking, watermark)
app/video.py        ffprobe / ffmpeg: probe, audio extract, burn-in, preview frame
app/jobs.py         Job store (on disk) + single worker queue + cleanup
static/             Web UI (plain HTML/CSS/JS, no build step)
fonts/              Bundled Bangla fonts (OFL licence)
scripts/            doctor, download_model, compare_models
tests/              pytest suite + browser e2e
```

Design decisions (jeno pore bhabte na hoy):
- **Ek-i worker thread**: ekbare ekta job (transcribe/render) — i9 overload hoy na, memory e ekta model.
- **Caption fonts bundle kora**: Mac e jei font-i thak, output ekrokom.
- **Sob kichu local**: video kothao pathano hoy na.
- **Subtitle text source of truth**: user edit korle word-timing auto-adjust hoy (same word count = purano timing rakhe).

---

## 8. Ekhon theke ki korbe — Roadmap

**Dina 1–2: Verify**
- [ ] Setup + doctor ✓
- [ ] 10–15 ta real Bangla clip diye `compare_models.py`; model thik koro
- [ ] 3 ta video full flow te caption kore Facebook Reels e post koro (nijer account)

**Shoptaho 1: Real user feedback**
- [ ] 10 jon creator/seller ke dekhao (Facebook group: "Bangladeshi Content Creators", F-commerce group). Tomar Mac e boshiye ba screen-share e use korao — bujho kothay atke.
- [ ] Note koro: kon kon word e beshi vul, ki style chay, koto second video (60s kafi na kom?)
- [ ] Watermark rekhe free dao — post e "কথন দিয়ে তৈরি" dekhle onno rao jante parbe (eta-i tomar organic growth)

**Shoptaho 2–3: Online kora (jodi feedback valo)**
- Beta: Mac e `./run.sh` + **Cloudflare Tunnel** (free) diye public URL. Shuru te 5–10 jon-er jonno ok. Mac on rakhte hobe.
- Pore: VPS (4–8 vCPU) ba GPU server. i9 e 1 minute video te kotokkhon lage seta measure kore hishab koro: koto video/ghonta handle korte parbe.
- **Je jinis ekhon NEI (online dewar age lagbe):** user account/login, per-user limit, payment, HTTPS, rate-limit, job cancel, privacy policy. Ekhon job ID-i access key (random 128-bit), kintu eta public internet e dewar moto security na.

**Mash 2: Income**
- Free: 30–60s, watermark. Paid: watermark chhara, lamba video (3–5 min), priority.
- Shuru te **manual**: bKash personal number e send money + TrxID diye verify (shuru e 10 jon paid user er jonno ei-i kafi).
- Pore payment gateway (bKash/Nagad support kora aggregator jemon SSLCommerz, aamarPay — shorto, fee, ar business document lagbe kina nije check koro).

**Pore (feature idea, impact order e):**
1. English/Banglish mixed caption + English translation (Whisper `translate`)
2. "Silence cut" (nirobota kete dewa) — Reels creator der priority
3. Brand kit (nijer font/color save), emoji
4. Batch upload, mobile-friendly PWA
5. Shorter edit: "puro text ek jaygay edit" mode

---

## 9. Legal / privacy (chhoto note)

- **Model:** `SayedShaun/bengali-whisper-medium-ct2` — model card e Apache-2.0; `large-v3`/`small` — MIT (OpenAI Whisper, Systran conversion). Business e use korar age nije licence page ekbar dekhe nio.
- **Font:** Hind Siliguri, Mina, Atma — SIL Open Font License (`fonts/OFL-*.txt`), free commercial use.
- **ffmpeg:** local e chalale kono somossa nei. Jodi pore nijer binary bundle kore distribute koro, tokhon ffmpeg licence (GPL/LGPL) dekhte hobe.
- **User video:** online dile ekta Privacy Policy lagbe ("24 ghonta pore delete hoy, kothao share kora hoy na") — ar ta sotti-i hote hobe.
- **Content:** user er upload kora video te copyright/obscene content thakte pare — Terms of Use e likhe rakho.
