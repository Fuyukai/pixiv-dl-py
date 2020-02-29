"""
Simple tag exploder. This loads every item in raw/ and "explodes" them into tag directories.
"""
import json
import pathlib
import sys
from collections import defaultdict

output_dir = pathlib.Path(sys.argv[1]).resolve()

# step 1: iterate over all in raw/ and build the buckets
buckets = defaultdict(list)
known_translations = {}


raw_dir = output_dir / "raw"
for subdir in raw_dir.iterdir():
    metadata = subdir / "meta.json"
    if not metadata.exists():
        continue

    data = json.loads(metadata.read_text())
    for tag in data["tags"]:
        buckets[tag["name"]].append(data["id"])
        if tag["translated_name"] is not None:
            known_translations[tag["name"]] = tag["translated_name"]

    print(f"Processed illust {data['id']}, {len(buckets)} buckets")

avg = sum(len(bucket) for bucket in buckets.values()) / len(buckets)
print(f"Average of {avg} illustration(s) per tag")

try:
    min_bucket_amt = int(sys.argv[2])
except IndexError:
    print("Skipping bucket filtering.")
else:
    print(f"Requiring minimum of {min_bucket_amt} in bucket...")
    new_buckets = {k: v for (k, v) in buckets.items() if len(v) >= min_bucket_amt}
    buckets = new_buckets
    print(f"Total buckets after filtering: {len(buckets)}")

avg = sum(len(bucket) for bucket in buckets.values()) / len(buckets)
print(f"Average of {avg} illustration(s) per tag post-filtering")

if input("Continue? [y/N] ").lower() != "y":
    exit(1)

tags_dir = output_dir / "tags"
for name, illust_ids in buckets.items():
    name = name.replace("/", "__")
    tag_dir = tags_dir / name
    tag_dir.mkdir(exist_ok=True)

    for id in illust_ids:
        raw_id_dir = raw_dir / str(id)
        target_dir = tag_dir / str(id)
        if target_dir.exists():
            continue

        target_dir.symlink_to(raw_id_dir, target_is_directory=True)
        print(f"{raw_id_dir} -> {target_dir}")

print("Saving translations...")
for original, english in known_translations.items():
    tag_dir = tags_dir / original

    if not tag_dir.exists():
        continue

    translation_file = tag_dir / "translation.json"

    data = json.dumps({"translated_name": english})
    translation_file.write_text(data)
