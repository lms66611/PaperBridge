# Third-party source references

This Git repository publishes the PaperBridge application source, the Zotero plugin,
the existing runtime lock, build instructions and the BabelDOC portable-cache patch.
Unmodified large third-party source archives and model weights are not checked into Git.
Their upstream URLs, exact build versions and SHA-256 values are recorded in
`source-manifest.json`. The patched BabelDOC file is retained under
`PaperBridge-modifications/`; `prepare_vendor.py` applies the documented cache patch.

If distributing a bundled binary, also supply the corresponding third-party sources
and notices required for that distribution. Application code is AGPL-3.0-or-later;
third-party resources retain their own licenses.
