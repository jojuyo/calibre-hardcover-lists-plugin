# Hardcover List

Calibre plugin for [Hardcover.app](https://hardcover.app/) list membership and
ISBN tools:

- **Hardcover Lists** — custom column and context menu for viewing and managing
  list membership
- **Cull ISBN** — scan book text for ISBN numbers and pick one to save

This repository contains a single self-contained plugin. The bundled GraphQL
client lives under `lib/graphql/` (Python module `hcl_graphql/`) and is included
in the plugin zip at build time.

## Optional: Hardcover metadata plugin

The official [**Hardcover** metadata
plugin](https://www.mobileread.com/forums/showthread.php?t=364041) is maintained
separately and can be installed from Calibre's plugin panel. It is **not** part
of this repo.

If you use both plugins:

- You can leave the API key blank in **Hardcover Lists** preferences — this
  plugin will read the key from the metadata plugin's config when available.
- API rate limits are shared via a lock file (see `lib/graphql/`).

## Project layout

```
src/hardcover_list/     Calibre plugin package (lists UI, config, cull ISBN)
lib/graphql/            Bundled Hardcover GraphQL client (module: hcl_graphql)
scripts/bundle.sh       Builds dist/hardcover-list-<version>.zip
```

## Setup

Requires [mise](https://mise.jdx.dev/), [uv](https://docs.astral.sh/uv/), and
[just](https://just.systems). Calibre is installed externally (or via
`just .calibre/source` during `just install`).

```bash
just install
just build
just install-plugin
```

Restart Calibre after installing. Configure your Hardcover API key under
**Preferences → Plugins → Hardcover Lists** (unless the metadata plugin already
provides one).

## Development

```bash
just test          # lib/graphql unit tests
just lint
just bump          # creates a hardcover-list-x.y.z git tag
```

Release tags use the prefix `hardcover-list-` (for example `hardcover-list-0.1.0`).

## License

GPL-3.0 — see [LICENSE](LICENSE). Derived from the upstream Hardcover metadata
plugin work by Rob Brazier; Hardcover List plugin by Juan York.
