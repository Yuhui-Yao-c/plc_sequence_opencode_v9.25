# -*- coding: utf-8 -*-
import os, sys, json

base = os.path.dirname(os.path.abspath(__file__))
source_dir = os.path.join(base, 'source', '转接片')
out_dir = os.path.join(source_dir, 'output', 'projects')
# Find the run directory
run_dir = None
for d in os.listdir(out_dir):
    if 'JMLEDQA10' in d:
        run_dir = os.path.join(out_dir, d)
        break

if not run_dir:
    print("Run dir not found")
    sys.exit(1)

# List all files in run_dir
with open(os.path.join(base, '_run_dir_listing.txt'), 'w', encoding='utf-8') as f:
    for root, dirs, files in os.walk(run_dir):
        for fname in sorted(files):
            rel = os.path.relpath(os.path.join(root, fname), run_dir)
            fsize = os.path.getsize(os.path.join(root, fname))
            f.write(f"{rel}\t{fsize}\n")

# Read B00001.input.json
batch_file = os.path.join(run_dir, 'batches', 'B00001.input.json')
if os.path.exists(batch_file):
    with open(batch_file, 'r', encoding='utf-8') as bf:
        data = json.load(bf)
    with open(os.path.join(base, '_b00001_preview.txt'), 'w', encoding='utf-8') as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2)[:8000])

# Read prediction_collection_pairs.csv
csv_file = os.path.join(run_dir, 'prediction_collection_pairs.csv')
if os.path.exists(csv_file):
    with open(csv_file, 'r', encoding='utf-8') as cf:
        content = cf.read()
    with open(os.path.join(base, '_collection_csv.txt'), 'w', encoding='utf-8') as f:
        f.write(content[:5000])

# Read prediction_candidate_pairs.csv header
cand_file = os.path.join(run_dir, 'prediction_candidate_pairs.csv')
if os.path.exists(cand_file):
    with open(cand_file, 'r', encoding='utf-8') as cf:
        lines = cf.readlines()
    with open(os.path.join(base, '_candidate_csv.txt'), 'w', encoding='utf-8') as f:
        f.write(f"Total lines: {len(lines)}\n")
        f.write("Header:\n")
        f.write(lines[0] if lines else "")
        f.write("\nFirst 5 data lines:\n")
        for line in lines[1:6]:
            f.write(line)

# Read workflow_manifest.json for batch info
manifest_file = os.path.join(run_dir, 'workflow_manifest.json')
with open(manifest_file, 'r', encoding='utf-8') as mf:
    manifest = json.load(mf)

# List batches_desc if exists
batches_desc_dir = os.path.join(run_dir, 'batches_desc')
desc_files = []
if os.path.isdir(batches_desc_dir):
    desc_files = sorted(os.listdir(batches_desc_dir))

with open(os.path.join(base, '_batch_info.txt'), 'w', encoding='utf-8') as f:
    f.write(f"Run dir: {run_dir}\n\n")
    f.write(f"Batches: {len(manifest.get('batches', []))}\n")
    for b in manifest.get('batches', []):
        f.write(f"  {b['batch_id']}: {len(b.get('candidate_ids',[]))} candidates, {len(b.get('event_ids',[]))} events\n")
    f.write(f"\nBatches_desc dir exists: {os.path.isdir(batches_desc_dir)}\n")
    f.write(f"Batches_desc files: {desc_files}\n")
    f.write(f"\nManifest status: {manifest.get('status')}\n")
    f.write(f"Candidate count: {manifest.get('candidate_count')}\n")
    f.write(f"Candidate pairs count: {manifest.get('candidate_pairs_count')}\n")
    f.write(f"Event count: {manifest.get('event_count')}\n")
