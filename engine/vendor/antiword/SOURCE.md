# Bundled antiword

This directory vendors the Windows x64 `antiword` executable used to extract
text from legacy OLE Word `.doc` files.

## Locked artifact

- Upstream project: Antiword
- Upstream version: `0.37` (21 October 2005)
- Windows package: MSYS2 UCRT64 `mingw-w64-ucrt-x86_64-antiword 0.37-3`
- Package URL:
  `https://repo.msys2.org/mingw/ucrt64/mingw-w64-ucrt-x86_64-antiword-0.37-3-any.pkg.tar.zst`
- Package SHA-256:
  `e5faf4c82a57ff1861d75c10fe3f51fb10c2dcb5d1a5d9fc7006701e3e23b8fb`
- `antiword.exe` SHA-256:
  `1dde37af1bb049c03e71c9d20eb99a2a3e011fe9170306b3a154be5c8515788f`

## Corresponding source

- Source archive: `antiword-0.37.tar.gz`
- Source archive SHA-256:
  `8e2c000fcbc6d641b0e6ff95e13c846da3ff31097801e86702124a206888f5ac`
- MSYS2 relocatable patch:
  `0001-Make-antiword.exe-relocatable-on-Windows.patch`
- Patch SHA-256:
  `11dee5957ffbe78537058274ef7fdacc0827080286f0cc21b1a76bca516cd89b`
- MSYS2 package recipe:
  `https://github.com/msys2/MINGW-packages/tree/master/mingw-w64-antiword`

## License

Antiword is distributed under the GNU General Public License version 2.
The complete license text is stored in `win-x64/LICENSE.txt`.
License file SHA-256:
`8177f97513213526df2cf6184d8ff986c675afb514d4e68a404010521b880643`.

The executable is invoked only as a local subprocess. No user document is sent
to a network service.
