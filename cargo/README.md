# flatpak-cargo-generator

Tool to automatically generate `flatpak-builder` manifest json from a `Cargo.lock`.

## Requirements

Poetry users can run `poetry install` and skip this.

Otherwise install Python 3.8+ with these modules:
- toml
- aiohttp
- (Optional) PyYAML>=6.0.2 for YAML output instead of JSON

Generated manifests are supported by flatpak-builder 1.2.x or newer.

## Usage

Poetry users: first activate your virtualenv by running `poetry shell`.

Convert the locked dependencies by Cargo into a format flatpak-builder can understand:
```
python3 ./flatpak-cargo-generator.py ./quickstart/Cargo.lock -o cargo-sources.json
```

The output file should be added to the manifest like
```json
{
    "name": "quickstart",
    "buildsystem": "simple",
    "build-commands": [
        "install -Dm644 cargo/config .cargo/config.toml",
        "cargo --offline fetch --manifest-path Cargo.toml --verbose",
        "cargo --offline build --release --verbose",
        "install -Dm755 ./target/release/quickstart -t /app/bin/"
    ],
    "sources": [
        {
            "type": "dir",
            "path": "."
        },
        "cargo-sources.json"
    ]
}
```

Make sure to override CARGO_HOME env variable to point it to `/run/build/$module-name/cargo` where `$module-name` is the flatpak module name, `quickstart` in this example.


For a complete example see the quickstart project.

## Development

1. Install Poetry v2 https://python-poetry.org/docs/#installation
2. `poetry install --with dev`
3. Format and lint: `poetry run ruff format && poetry run ruff check --fix --exit-non-zero-on-fix`
4. Type check: `poetry run mypy .`
