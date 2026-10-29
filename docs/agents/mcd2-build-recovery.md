# Minecraft Dungeons II: build integrity, visibility and recovery contract

The one supported real-game pipeline is **`MCD2 observable recovery build`**
(`.github/workflows/mcd2-observed.yml`). It is **manual-only**, since the
inputs are large and the target game data and keys must remain private. The
legacy unobserved `mcd2-integrated.yml` Actions workflow has been retired.

The pipeline now builds the **iOS target directly**, including the Mac helper
needed to decode Unreal/Oodle assets, without compiling a complete desktop
shader library first. Run `40888376530` spent 75 minutes plus a ten-minute
recovery in desktop shader compilation, then reached iOS packaging just
before the old 55-minute iOS deadline. The iOS pass now has a finite
three-hour budget and one same-runner recovery capped at ten minutes, inside
a four-hour job. Idle-process detection and the 20-second heartbeat remain.
This is a measured packaging-budget change, not a fix for engine allocation
or proof that the game boots. Mac gameplay and iPad gameplay are untested.

## Returning the IPA to the owner's Drive

`workflow_dispatch` takes `output_file`: the ID of a **dedicated binary
placeholder owned by the user**, named `MinecraftDungeonsII.ipa.pending`.
Grant the existing runner service account Editor access to that file. Never
use a game input, keys file, document, or unrelated file as the output.
The workflow checks edit access before downloading the games. It does not
change permissions or create service-account-owned files.

`scripts/mcd2_drive_output.py` requests Drive write scope and updates this
existing file through resumable `files.update`, preserving the user's
ownership and storage quota. The dedicated file becomes
`MinecraftDungeonsII.ipa` after upload. It is not a playable IPA while it
still contains the placeholder. No additional OAuth user credential is
needed for this existing-file update route, but a real runner upload must
still verify the service account can write content and the owner has quota.

Before delivery the script checks the IPA's single app payload, executable,
bundled game resources, Metal libraries and every ZIP CRC. It then compares
the stored Drive size and checksum with the local archive. Upload success
alone is not a successful boot. The public build report precedes delivery;
use the separate **Drive delivery step** for verified delivery status.

The original input-download credential remains read-only in its downloader.
The output script uses the same service account with write scope, restricted
by the service account's actual file permissions and the dedicated filename
gate. User authorization for this file does not grant access to all Drive.
The output is separate from the runner cleanup; the owner can download it
on the iPad, install it with their existing LiveContainer/signing setup and
return the app's `Documents/log.txt` for actual device validation.

## Mandatory checks for every subsequent MCD2 workflow

1. **Input gate:** download the exact base and v65536 update through the
   authenticated Drive API; both must match their expected byte counts
   (1,662,600,274 and 2,943,659,595) **and** the per-file MD5 checksum
   returned by Google Drive for each binary upload. Verify every byte, not
   just that the URL responded. A missing checksum, short response, wrong
   content, failed transfer or unsupported MIME type must **stop** the
   build *before* extraction. Existing local downloads are rehashed.
2. **Preflight gate:** run `scripts/mcd2_preflight.py` against the verified
   NSPs and private keys before the game pipeline.
3. **Visibility:** the observed runner emits safe stage names, stage duration,
   numeric counters when supplied, 20-second heartbeats, CPU usage, and
   inactivity warnings. CPU activity is **not** evidence of forward progress.
   Warn if the log is silent or measured counters stop advancing. A
   confirmed idle process is terminated after watchdog thresholds, with
   its **last observed stage** in the text report. Long working shader
   compilation is not killed merely for taking time, but still has a
   finite wall-clock limit.
4. **Verified local checkpoints:** `scripts/mcd2_checkpoint.py` checks real
   outputs and success/fingerprint stamps before labelling extraction,
   rewritten modules, shader packages, Metal libraries and host as reusable.
   **Do not** treat stage-end JSON alone as a checkpoint. The actual
   `build_ipa_from_nsp.py` fingerprint/reuse checks remain authoritative;
   a checkpoint marker does not let a later run skip validation.
5. **Recovery:** on a timeout, terminate the full child process group,
   preserve only the *current runner's* completed output, and retry the iOS
   build at most once, reusing outputs if the builder validates them. Fail
   with an explicit last-stage reason if no progress can be made.
6. **Privacy:** never publish NSPs, extracted/rewritten game code, shader
   output, product keys, raw build logs, unsigned IPA or credentials in a
   public Actions artifact/cache. Only sanitised progress and metadata-only
   checkpoint indices may be uploaded.

## What survives GitHub Actions termination?

Local shader caches, derived profiles, downloaded files and intermediate
modules **do not** survive termination of a GitHub-hosted runner. The small
checkpoint-index artifact is a diagnosis/history record, **not** a backup
of the missing outputs. It cannot be used to resume a subsequent job on
another ephemeral Mac. The automatic retry **within one runner** is real
and uses the build system's fingerprint-validated files.

To enable **true cross-run binary resume**, first configure an owner-approved
private persistent object store or a persistent self-hosted Apple-Silicon
runner. The secure restore process must check the exact source file IDs,
versions, toolchain, content hashes and stage fingerprints before reuse;
until then cross-run resume is **not enabled**. No public repository upload
is a substitute for private storage.

## Useful CI markers

- `[DRIVE API] NSP 1/2 ... checksum ... verified`: base input passed
- `[DRIVE API] NSP 2/2 ... checksum ... verified`: update input passed
- `[OBSERVED] phase: ...`: tool-reported current stage
- `[HEARTBEAT] ...`: stage duration, process CPU, log age, counter age
- `[NO-PROGRESS] ...`: suspect stall; not by itself proof of a deadlock
- `[CHECKPOINT] verified ...`: actual outputs are present and stamped
- `[STAGE] completed ...; no reusable checkpoint verified`: no resume proof
- `[WATCHDOG] ...`: confirmed idle timeout or finite stage budget reached
- `[RECOVERY] ...`: one reattempt with existing same-runner outputs

Unit checks live in `scripts/tests/test_mcd2_*.py`; the workflow runs these
and the repository's normal lint checks before processing the game.
