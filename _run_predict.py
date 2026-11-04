# -*- coding: utf-8 -*-
import os, sys, subprocess, json

base = os.path.dirname(os.path.abspath(__file__))
source_dir = os.path.join(base, 'source', '转接片')

# Find the XML file
xml_file = None
for f in os.listdir(source_dir):
    if f.endswith('.xml') and 'JMLEDQA10' in f:
        xml_file = os.path.join(source_dir, f)
        break

out_dir = os.path.join(source_dir, 'output', 'projects', os.path.splitext(os.path.basename(xml_file))[0])

# Check existing manifest
manifest_path = os.path.join(out_dir, 'workflow_manifest.json')
if os.path.exists(manifest_path):
    with open(manifest_path, 'r', encoding='utf-8') as f:
        manifest = json.load(f)
    with open(os.path.join(base, '_manifest_check.txt'), 'w', encoding='utf-8') as f:
        f.write(json.dumps(manifest, ensure_ascii=False, indent=2))

# Run predict with --force
cmd = [
    sys.executable, 'plc_cli.py', 'predict',
    '--source', xml_file,
    '--pattern', 'EM*.Process_step[*]',
    '--out', out_dir,
    '--force',
]

result = subprocess.run(cmd, capture_output=True, text=True, encoding='utf-8', errors='replace', cwd=base)

# Write output to file to avoid console encoding issues
with open(os.path.join(base, '_predict_output.txt'), 'w', encoding='utf-8') as f:
    f.write("=== STDOUT ===\n")
    f.write(result.stdout)
    f.write("\n=== STDERR ===\n")
    f.write(result.stderr)
    f.write("\n=== Return code: ")
    f.write(str(result.returncode))
    f.write(" ===\n")
