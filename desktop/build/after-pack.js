// electron-builder `afterPack` hook — runs after the .app is assembled, before it's signed.
//
// Strips extended attributes from the packed bundle. The PyInstaller backend we copy into
// Resources/backend comes out of a build tree full of quarantine/Finder xattrs, and codesign
// refuses those files with "resource fork, Finder information, or similar detritus not
// allowed" — which would fail the whole signed build. Cheap insurance, no-op when clean.

const path = require('path')
const { execFileSync } = require('child_process')

exports.default = async function afterPack(context) {
  if (context.electronPlatformName !== 'darwin') return
  const appPath = path.join(context.appOutDir, `${context.packager.appInfo.productFilename}.app`)
  execFileSync('xattr', ['-cr', appPath], { stdio: 'inherit' })
  console.log(`  • cleared extended attributes  path=${appPath}`)
}
