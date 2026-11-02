"""Prepare a self-contained local Cubism runtime using the supplied Core."""

import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
PACKAGES = (
    ("pixi.js", "6.5.10", "package/dist/browser/pixi.min.js", "pixi.min.js"),
    ("pixi-live2d-display", "0.4.0", "package/dist/cubism4.min.js", "cubism4.min.js"),
)


def model_references(settings):
    refs = settings["FileReferences"]
    paths = [refs["Moc"], *refs["Textures"]]
    paths.extend(refs[key] for key in ("Physics", "Pose", "DisplayInfo", "UserData") if refs.get(key))
    paths.extend(item["File"] for item in refs.get("Expressions", []))
    paths.extend(item["File"] for group in refs.get("Motions", {}).values() for item in group)
    return paths


def download_package(name, version, member, output, target):
    with urllib.request.urlopen(f"https://registry.npmjs.org/{name}/{version}", timeout=30) as response:
        metadata = json.load(response)
    with urllib.request.urlopen(metadata["dist"]["tarball"], timeout=60) as response:
        archive = response.read()
    algorithm, expected = metadata["dist"]["integrity"].split("-", 1)
    actual = base64.b64encode(hashlib.new(algorithm, archive).digest()).decode("ascii")
    if actual != expected:
        raise ValueError(f"{name}: package integrity mismatch")
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as package:
        (target / output).write_bytes(package.extractfile(member).read())
        for entry in package.getmembers():
            if entry.isfile() and "license" in Path(entry.name).name.lower():
                (target / f"{name}-{Path(entry.name).name}").write_bytes(package.extractfile(entry).read())


def prepare(model, core, target):
    model = Path(model).resolve(strict=True)
    core = Path(core).resolve(strict=True)
    target = Path(target).resolve()
    if core.suffix != ".js":
        raise ValueError("Core must point to an installed live2dcubismcore JS file")
    settings = json.loads(model.read_text(encoding="utf-8-sig"))
    sources = [(model, Path(model.name))]
    for name in model_references(settings):
        source = (model.parent / name).resolve(strict=True)
        if not source.is_relative_to(model.parent):
            raise ValueError(f"Model resource outside its directory: {name}")
        sources.append((source, source.relative_to(model.parent)))
    if model.is_relative_to(target):
        raise ValueError("Target must not contain the source model")
    target.mkdir(parents=True, exist_ok=True)
    for name, version, member, output in PACKAGES:
        download_package(name, version, member, output, target)
    for source, relative in sources:
        destination = target / "model" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
    core_destination = target / "live2dcubismcore.min.js"
    if core != core_destination:
        shutil.copy2(core, core_destination)
    config = {"model": (Path("model") / model.name).as_posix(), "core": core_destination.name}
    (target / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", required=True, type=Path)
    parser.add_argument("--core", required=True, type=Path)
    parser.add_argument("--target", type=Path, default=ROOT / "Live2D")
    args = parser.parse_args()
    config = prepare(args.model, args.core, args.target)
    print(json.dumps(config, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
