from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple, Union

import enum
import json
import subprocess

from pytest_httpserver import HTTPServer

import pytest

from flatpak_node_generator.cache import Cache, FilesystemBasedCache, NullCache
from flatpak_node_generator.manifest import ManifestGenerator
from flatpak_node_generator.providers import ProviderFactory
from flatpak_node_generator.providers.npm import (
    NpmLockfileProvider,
    NpmModuleProvider,
    NpmProviderFactory,
)
from flatpak_node_generator.providers.special import SpecialSourceProvider
from flatpak_node_generator.providers.yarn import YarnProviderFactory
from flatpak_node_generator.requests import Requests

Requests.retries = 3


@pytest.fixture(autouse=True)
def fs_cache(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    Cache.instance = FilesystemBasedCache(tmp_path_factory.mktemp('fs_cache'))

    try:
        yield
    finally:
        Cache.instance = NullCache()


@dataclass
class RequestsController:
    server: HTTPServer

    def url_for(self, suffix: str) -> str:
        return self.server.url_for(suffix)  # type: ignore

    @property
    def url(self) -> str:
        return self.url_for('')


@pytest.fixture
def requests() -> Iterator[RequestsController]:
    with HTTPServer() as server:
        yield RequestsController(server)
        server.check_assertions()


_DEFAULT_MODULE = 'module'
_DEFAULT_NODE = 'node16'


@dataclass
class FlatpakBuilder:
    root: Path

    @property
    def build_dir(self) -> Path:
        return self.root / 'build'

    @property
    def state_dir(self) -> Path:
        return self.root / 'state'

    @property
    def manifest_file(self) -> Path:
        return self.root / 'manifest.json'

    @property
    def module_dir(self) -> Path:
        return self.state_dir / 'build' / _DEFAULT_MODULE

    @property
    def runtime_module_dir(self) -> Path:
        return Path('/run/build') / _DEFAULT_MODULE

    def build(
        self,
        *,
        sources: Iterable[Dict[Any, Any]],
        commands: List[str] = [],
        use_node: Optional[Union[str, bool]] = None,
    ) -> None:
        if use_node == True:
            use_node = _DEFAULT_NODE

        sdk_extensions = []
        build_options = {}

        if use_node:
            sdk_extensions.append(f'org.freedesktop.Sdk.Extension.{use_node}')
            build_options['env'] = {
                'XDG_CACHE_HOME': str(
                    self.runtime_module_dir / 'flatpak-node' / 'cache'
                ),
                'npm_config_cache': str(
                    self.runtime_module_dir / 'flatpak-node' / 'npm-cache'
                ),
            }

            for i, command in enumerate(commands):
                commands[i] = f'. /usr/lib/sdk/{use_node}/enable.sh && {command}'

        manifest = {
            'id': 'com.test.Test',
            'runtime': 'org.freedesktop.Platform',
            'runtime-version': '21.08',
            'sdk': 'org.freedesktop.Sdk',
            'sdk-extensions': sdk_extensions,
            'modules': [
                {
                    'name': _DEFAULT_MODULE,
                    'buildsystem': 'simple',
                    'build-commands': commands,
                    'build-options': build_options,
                    'sources': list(sources),
                }
            ],
        }

        with self.manifest_file.open('w') as fp:
            json.dump(manifest, fp, indent=4)

        subprocess.run(
            [
                'flatpak-builder',
                '--build-only',
                '--keep-build-dirs',
                f'--state-dir={self.state_dir}',
                str(self.build_dir),
                str(self.manifest_file),
            ],
            check=True,
        )


@pytest.fixture
def flatpak_builder(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[FlatpakBuilder]:
    yield FlatpakBuilder(tmp_path_factory.mktemp('flatpak-builder'))


class ProviderFactoryType(enum.Enum):
    NPM = enum.auto()
    YARN = enum.auto()


@dataclass
class ProviderPaths:
    type: ProviderFactoryType
    root: Path

    @property
    def package_json(self) -> Path:
        return self.root / 'package.json'

    @property
    def lockfile(self) -> Path:
        if self.type == ProviderFactoryType.NPM:
            return self.root / 'package-lock.json'
        elif self.type == ProviderFactoryType.YARN:
            return self.root / 'yarn.lock'
        else:
            assert False, self.type

    def add_to_manifest(self, gen: ManifestGenerator) -> None:
        gen.add_local_file_source(self.package_json)
        gen.add_local_file_source(self.lockfile)
        if self.type == ProviderFactoryType.YARN:
            gen.add_data_source(
                f'yarn-offline-mirror "./flatpak-node/yarn-mirror"', Path('.yarnrc')
            )


@dataclass
class ProviderFactorySpec:
    datadir: Path
    type: ProviderFactoryType
    special: SpecialSourceProvider.Options = SpecialSourceProvider.Options(
        node_chromedriver_from_electron=None,
        electron_ffmpeg=None,
        electron_node_headers=False,
        nwjs_version=None,
        nwjs_node_headers=False,
        nwjs_ffmpeg=False,
        xdg_layout=True,
    )

    def create_factory(
        self,
        lockfile_root: str,
        npm_lockfile: Optional[NpmLockfileProvider.Options] = None,
        npm_module: Optional[NpmModuleProvider.Options] = None,
    ) -> Tuple[ProviderFactory, ProviderPaths]:
        paths = ProviderPaths(
            type=self.type, root=self.datadir / 'packages' / lockfile_root
        )

        if self.type == ProviderFactoryType.NPM:
            if npm_lockfile is None:
                npm_lockfile = NpmLockfileProvider.Options(no_devel=False)
            if npm_module is None:
                npm_module = NpmModuleProvider.Options(
                    registry='https://registry.npmjs.org',
                    no_autopatch=False,
                    no_trim_index=False,
                )

            return (
                NpmProviderFactory(
                    lockfile_root=paths.root,
                    options=NpmProviderFactory.Options(
                        lockfile=npm_lockfile,
                        module=npm_module,
                    ),
                ),
                paths,
            )
        elif self.type == ProviderFactoryType.YARN:
            return YarnProviderFactory(), paths
        else:
            assert False, self.type

    async def generate_modules(
        self,
        lockfile_root: str,
        gen: ManifestGenerator,
        npm_lockfile: Optional[NpmLockfileProvider.Options] = None,
        npm_module: Optional[NpmModuleProvider.Options] = None,
    ) -> ProviderPaths:
        factory, paths = self.create_factory(
            lockfile_root, npm_lockfile=npm_lockfile, npm_module=npm_module
        )
        special = SpecialSourceProvider(gen, self.special)

        module = factory.create_module_provider(gen, special)
        for package in factory.create_lockfile_provider().process_lockfile(
            paths.lockfile
        ):
            await module.generate_package(package)

        paths.add_to_manifest(gen)
        return paths

    @property
    def install_command(self) -> str:
        if self.type == ProviderFactoryType.NPM:
            return 'npm install --offline'
        elif self.type == ProviderFactoryType.YARN:
            return 'yarn --offline'
        else:
            assert False, self.type


@pytest.fixture
def npm_provider_factory_spec(shared_datadir: Path) -> ProviderFactorySpec:
    return ProviderFactorySpec(datadir=shared_datadir, type=ProviderFactoryType.NPM)


@pytest.fixture
def yarn_provider_factory_spec(shared_datadir: Path) -> ProviderFactorySpec:
    return ProviderFactorySpec(datadir=shared_datadir, type=ProviderFactoryType.YARN)


@pytest.fixture(params=[ProviderFactoryType.NPM, ProviderFactoryType.YARN])
def provider_factory_spec(request: Any, shared_datadir: Path) -> ProviderFactorySpec:
    type = request.param
    assert isinstance(type, ProviderFactoryType)
    return ProviderFactorySpec(datadir=shared_datadir, type=type)
