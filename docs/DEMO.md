# Demo: 3-minute narration script

**Video:** [demo/file-engine-demo.mp4](demo/file-engine-demo.mp4) (3:02, 1280×720, captions burnt in, silent audio track for player compatibility).
Rebuild it after any change with `PYTHONPATH=src python3 docs/demo/make_video.py`. Every scene is drawn from a real run (generate → dry run → apply → re-run → undo, done fresh each time) and from `evals/results/`. The script asserts that undo restored the folder before it writes the video.

**To add your voice:** read each block at a relaxed pace (about 150 words a minute; each block fits its scene). Record it as one file, then:

```bash
ffmpeg -i docs/demo/file-engine-demo.mp4 -i voice.m4a -map 0:v -map 1:a -c:v copy -c:a aac -shortest docs/demo/file-engine-demo-voice.mp4
```

---

**0:00-0:10 · Title**
> This is file-automation-engine. It tidies a messy folder: finds duplicates, renames files, sorts them into folders and writes a report, without ever losing a file.

**0:10-0:26 · The mess**
> The demo starts by generating a realistic mess: a thousand files with copies named "copy of" and "(1)", near-copies built to fool lazy tools, accents, emoji, hidden files and symlinks. It also writes an answer key, so every result can be checked.

**0:26-0:44 · How it works**
> It works in two halves. The planner only reads: it scans without following shortcuts, finds duplicates by size, then the first and last eight kilobytes, then a full SHA-256, and decides where everything goes. The executor only carries out a finished plan. So the dry run can't disagree with the real run.

**0:44-1:02 · Dry run**
> By default it's a dry run. Eight hundred and thirty-four files to move and rename, a hundred and eighty duplicate copies to quarantine, four things it deliberately won't touch. Nothing on disk has changed.

**1:02-1:18 · The report**
> The same plan comes out as a Markdown report, JSON and a CSV of every action. It found all the copies while reading about a third of the bytes, because most files have a size nobody else shares.

**1:18-1:36 · Apply, again, undo**
> With apply, every move is written to a journal and synced to disk first, then made in a way that can never overwrite. Run it again: nothing to do. Then undo: every file goes back where it was.

**1:36-1:50 · Before and after**
> Before: Desktop, Downloads, tmp. After: Images, Documents, Archives and the rest, with clean dated names, and copies in a duplicates folder you empty when you're happy.

**1:50-2:12 · Hard numbers**
> On twenty held-out folders, forty thousand files: one hundred percent precision and recall on duplicates, none of eight hundred traps fooled it, zero bytes lost, and undo exact every time. Half a second to plan five thousand files, zero cost, and zero of sixteen attacks succeed.

**2:12-2:30 · Each defence switched off**
> File names are text other people control. To prove each defence matters, each is switched off and its attacks get through: escaping the folder, overwriting your file, hijacking the terminal. Where one doesn't, a deeper layer is named.

**2:30-2:50 · Postmortem**
> The first full run skipped half the duplicates. Each copy's keeper had already been moved, so the safety re-check refused. Correct check, wrong order. Now copies go first. I caught it because the second run still had work to do.

**2:50-3:02 · Next**
> Next: a fresh set of real file names, a hash cache for faster re-runs, and faster journaling. The design doc, postmortem and a line-by-line cheatsheet are in the repo.
