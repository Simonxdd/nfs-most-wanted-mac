// svgrender: draws an SVG with macOS's own renderer (AppKit NSImage / CoreSVG), headless, for the HD HUD
// (tools/hd_pack/ui2_pack.py). Writes raw RGBA bytes (row 0 = top) with straight (non-premultiplied) alpha.
//   svgrender <in.svg> <out.rgba> <width> <height>
import AppKit

let a = CommandLine.arguments
guard a.count == 5, let w = Int(a[3]), let h = Int(a[4]), w > 0, h > 0 else {
    FileHandle.standardError.write("usage: svgrender <in.svg> <out.rgba> <width> <height>\n".data(using: .utf8)!)
    exit(2)
}
guard let img = NSImage(contentsOfFile: a[1]) else {
    FileHandle.standardError.write("svgrender: cannot read \(a[1])\n".data(using: .utf8)!)
    exit(1)
}
guard let rep = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: w, pixelsHigh: h, bitsPerSample: 8,
                                 samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                                 bytesPerRow: w * 4, bitsPerPixel: 32) else { exit(1) }
NSGraphicsContext.saveGraphicsState()
let ctx = NSGraphicsContext(bitmapImageRep: rep)!
NSGraphicsContext.current = ctx
ctx.imageInterpolation = .high
NSColor.clear.set()
NSRect(x: 0, y: 0, width: w, height: h).fill(using: .copy)
img.draw(in: NSRect(x: 0, y: 0, width: w, height: h), from: .zero, operation: .sourceOver, fraction: 1)
ctx.flushGraphics()
NSGraphicsContext.restoreGraphicsState()
// The bitmap is premultiplied: un-premultiply, then write the raw RGBA.
let p = rep.bitmapData!
for i in stride(from: 0, to: w * h * 4, by: 4) {
    let al = Int(p[i + 3])
    if al > 0 && al < 255 {
        for c in 0..<3 { p[i + c] = UInt8(min(255, (Int(p[i + c]) * 255 + al / 2) / al)) }
    }
}
let raw = Data(bytes: p, count: w * h * 4)
do {
    try raw.write(to: URL(fileURLWithPath: a[2]))
} catch {
    FileHandle.standardError.write("svgrender: cannot write \(a[2])\n".data(using: .utf8)!)
    exit(1)
}
