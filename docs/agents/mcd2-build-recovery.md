# Minecraft Dungeons II: build integrity, visibility and recovery contract

The basic real-game entry point is **`MCD2 observable recovery build`**
(`.github/workflows/mcd2-observed.yml`). The optional **`MCD2 observable backup
build`** (`.github/workflows/mcd2-backup.yml`) uses the same observed builder
and adds readable live status and partial Metal-cache recovery. Both are
**manual-only** on main, since the
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

## Verified upload failure and complete preparation recovery

Run `40921864076` successfully built its IPA in 3,768 seconds. Archive
validation passed for all 4,738,770,140 bytes. The next operation failed:
Drive multi-chunk upload. Cleanup then deleted the ephemeral runner's IPA;
that run has no recoverable binary output. This was delivery failure, not
an archive-integrity or shader-compilation failure.

A separately reproduced defect in the custom checkpoint transport is
`httplib2` interpreting Drive's HTTP **308 Resume Incomplete** as an HTTP
redirect and requiring a Location header. Drive
uses 308 to acknowledge intermediate upload chunks. The shared
`scripts/mcd2_drive_transport.py` removes only 308 from the transport's
redirect codes, retaining ordinary redirects. A real synthetic upload of
40 MiB + one byte reproduced `RedirectMissingLocation` with the old
transport and passed stored size/MD5 verification with this fix. A smaller
single-chunk probe had passed and was insufficient.

Backup run `40927615593` subsequently succeeded using the existing IPA
uploader: its archive passed CRC validation, and Drive delivery independently
verified stored size/MD5 for **4,738,770,141 bytes** at 19:17 UTC. The first
run's generic upload error did not record enough detail to prove it shared
the checkpoint transport defect. The delivered backup is available; no
additional full build is necessary just to obtain an IPA. Device boot is
still untested. The partial-cache worker reported failed subsequent
backups; successful IPA delivery does not establish full-stage recovery.

**`MCD2 resumable prepared build`** (`.github/workflows/mcd2-resumable.yml`)
checks the same multi-chunk write before downloading game data. It has its
own status/output files and concurrency group. In order it:

1. Verifies the inputs and attempts a compatible complete-stage restore.
2. If none exists, runs the normal iOS preparation through full shader
   completion, then saves and verifies the prepared code, selected profile
   including generated tables, Metal libraries, completion fingerprints,
   inputs stamps and reflection build summary in private Drive storage.
3. Only after this checkpoint is verified does it compile the app and pack
   the IPA. A restored run skips rewriting and shader translation/compile,
   but re-extracts the merged game resources from the verified NSPs.
4. Validates and uploads the IPA, then checks Drive size/MD5 independently.

The two rotating prepared archive slots are separate from the earlier
partial-cache slots. The complete archive has a 12 GiB payload limit and
200,000-file limit, consumes the owner's Drive quota, and contains private
derived game data. It excludes NSPs, keys, RomFS and raw logs. The index is
committed only after the new slot's stored size/MD5 passes; a failed upload
cannot replace the previous indexed generation. Restore checks archive
size/MD5, safe regular paths, exact member manifest, every SHA-256 and the
completed-stage contract before publishing any file.

Reuse requires the exact verified input IDs/MD5s/sizes, compiler/SDK and
chain plus a content digest of preparation dependencies (host/graphics/
rewriter/profile tools and the NSP preparation script). An unrelated
packaging/transport/workflow commit does not by itself invalidate shaders.
Changes to these preparation dependencies require a fresh preparation.
Repack uses the portable selected profile, and `build_ipa.sh` still validates
its shader inputs and generated table stamps. No marker force-skips those
checks. This adds a completed-stage boundary, not arbitrary line-by-line
resume. The first successful real-game checkpoint and fresh-run restoration
still require observation; synthetic restore tests do not establish that.

Shader progress now counts **distinct completed output keys**, rather than
the highest sorted index observed from out-of-order parallel workers.
Final variants, generated reflection and completion stamps can still
follow the numeric counter. Live status also reports only recognized child
tool roles, their CPU/age and output counts/sizes; raw process arguments
never leave the runner. CPU and unchanged counters are observations, not
proof of a stall or completion.

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

The following limitation applies to the **basic** workflow. The backup
workflow saves the limited Metal compile cache described below, not whole
extraction/rewrite stages.

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
until then cross-run resume is **not enabled in the basic workflow**.
The separate complete-preparation workflow above implements a private
completed-stage boundary; the older backup workflow below saves only a
partial Metal cache. No public repository upload
is a substitute for private storage.

## Optional readable status and partial shader-cache backup

The backup workflow has its own concurrency group, Drive output file and
four user-owned Drive state files, shared with the existing service account:
`MCD2-live-status.json`, `MCD2-cache-index.json`, and two cache archive slots.
It cannot cancel the basic run or overwrite that run's IPA. Starting both
still repeats downloads and compilation and uses another Mac runner.

`scripts/mcd2_cloud.py` publishes a small status document approximately
every 20 seconds during the build. It includes the whitelisted phase,
numeric done/total counters when present, elapsed/phase time, CPU reading,
time since log/counter changes, and separate **sampled_at** and
**published_at** timestamps. A fresh upload with an old sample is not proof
that the watcher is advancing. Raw logs and game content are never copied
into the document. Each run ID is recorded; stale timestamps mean visibility
is unavailable, not that the build succeeded. Status publication uses a
separate thread, bounded network calls, and does not replace the local idle
watchdog or finite build deadline. On completion the final workflow step
marks delivery success/failure; `packaged` alone does not claim delivery.

Every ten minutes, and after the complete iOS library stage, an independent
worker snapshots only atomically published, content-keyed `.metallib` files
under `shader/cache/cas/metallib`. No `.key` stage-completion marker, key
file, NSP, RomFS or raw log is archived. This deliberately saves expensive
Metal compilations while earlier extraction and rewrite may run again. It
does not skip translation, claim a completed library stage, or fix the
game's allocator. The normal Metal cache key still decides whether any
restored library matches its MSL, compile convention and compiler.

Snapshots are bounded to 512 MiB of library payload, 100,000 entries, and ten
minutes per network transfer. They carry a manifest with each file's size
and SHA-256, exact source revision, the verified input Drive IDs/MD5s/sizes,
compiler/SDK identity and shader chain. Restores require matching identity,
stored and downloaded archive size/MD5, safe regular-file paths, matching
manifest membership and every SHA-256 before publishing any cache entry.
All normal builder fingerprints remain authoritative. If the cache exceeds
the payload budget, the snapshot contains a subset; omitted libraries are
compiled normally. Two archive slots use roughly at most 1 GiB plus archive
metadata, rather than backing up the entire multi-GB game workspace.

The uploader writes the other archive slot, verifies its stored checksum,
then commits and reads back the small checkpoint index. An interrupted
upload leaves the previous indexed generation available. The next upload
re-reads the index after lost responses before selecting a slot. Both slots
consume the owner's Drive quota, and periodic snapshots add disk/network
work. Quota/network errors appear in status and leave the build running
with its local cache; they do not establish durable recovery. Abrupt job
termination can still lose work since the last verified snapshot.

Synthetic tests exercise live status, safe archive restore, a fresh-session
cache restore, content corruption, incompatible toolchains, unsafe paths,
interrupted uploads and lost checkpoint-index responses. These tests do not
establish a successful real-game cache restore on another GitHub runner;
that remains a measured validation step for the first backup run.

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
