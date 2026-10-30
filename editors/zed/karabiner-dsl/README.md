# Karabiner DSL for Zed

A Zed language extension for highlighting `.kb` files.

From a clone of this repository, run:

```bash
just zed-check
```

Then use the Zed command palette action `zed: install dev extension` and
select the `editors/zed/karabiner-dsl` directory inside your clone.

The check regenerates the grammar, builds it in `.build/`, and tests the
highlight queries against the sample files. Run it again after changing the
grammar or highlight queries. Zed fetches the grammar from the
`cornapps/karabiner-dsl` repository on GitHub when it installs the extension.

The grammar is permissive and supplies editing feedback. To check the
meaning of a file, run this from the repository root:

```bash
uv run python -m karabiner_dsl --lint config.kb
```

See the [language guide](../../../GUIDE.md) for syntax and examples.
