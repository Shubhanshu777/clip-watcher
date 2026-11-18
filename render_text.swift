// render_text: draws bold outlined caption images (transparent PNGs) for polish_clip.py.
// ffmpeg on this machine has no text filters, so macOS AppKit does the text rendering instead.
//
// Input (stdin): JSON array of items. Output: one PNG per item.
//   out        output path
//   words      words to draw on one line (already upper-cased)
//   highlight  index of the word drawn in yellow; -1 = none, -2 = all yellow
//   size       font size in points
//   width/height  canvas size in pixels
//   scale      pop-in scale applied around the canvas center (1.0 = normal)
import AppKit
import Foundation

struct Item: Decodable {
    let out: String
    let words: [String]
    let highlight: Int
    let size: CGFloat
    let width: Int
    let height: Int
    let scale: CGFloat
}

let yellow = NSColor(calibratedRed: 1.0, green: 0.84, blue: 0.10, alpha: 1.0)

func attributes(color: NSColor, size: CGFloat) -> [NSAttributedString.Key: Any] {
    let font = NSFont(name: "Impact", size: size) ?? NSFont.boldSystemFont(ofSize: size)
    return [
        .font: font,
        .foregroundColor: color,
        .strokeColor: NSColor.black,
        .strokeWidth: -9.0,  // negative = fill AND stroke (outline thickness as % of font size)
    ]
}

func render(_ item: Item) {
    let rep = NSBitmapImageRep(
        bitmapDataPlanes: nil, pixelsWide: item.width, pixelsHigh: item.height,
        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: rep)
    defer { NSGraphicsContext.restoreGraphicsState() }

    if !item.words.isEmpty {
        // Shrink the font until the whole line fits inside 92% of the canvas width.
        var fontSize = item.size
        func lineWidth(_ s: CGFloat) -> CGFloat {
            let spaceWidth = s * 0.26
            let wordWidths = item.words.map {
                NSAttributedString(string: $0, attributes: attributes(color: .white, size: s)).size().width
            }
            return wordWidths.reduce(0, +) + spaceWidth * CGFloat(max(0, item.words.count - 1))
        }
        while lineWidth(fontSize) > CGFloat(item.width) * 0.92 && fontSize > 20 { fontSize -= 4 }

        let context = NSGraphicsContext.current!.cgContext
        context.translateBy(x: CGFloat(item.width) / 2, y: CGFloat(item.height) / 2)
        context.scaleBy(x: item.scale, y: item.scale)
        context.translateBy(x: -CGFloat(item.width) / 2, y: -CGFloat(item.height) / 2)

        let shadow = NSShadow()
        shadow.shadowColor = NSColor.black.withAlphaComponent(0.6)
        shadow.shadowBlurRadius = 10
        shadow.shadowOffset = NSSize(width: 0, height: -5)
        shadow.set()

        var cursorX = (CGFloat(item.width) - lineWidth(fontSize)) / 2
        for (index, word) in item.words.enumerated() {
            let isYellow = item.highlight == -2 || index == item.highlight
            let drawn = NSAttributedString(
                string: word, attributes: attributes(color: isYellow ? yellow : .white, size: fontSize))
            let wordSize = drawn.size()
            drawn.draw(at: NSPoint(x: cursorX, y: (CGFloat(item.height) - wordSize.height) / 2))
            cursorX += wordSize.width + fontSize * 0.26
        }
    }

    let png = rep.representation(using: .png, properties: [:])!
    try! png.write(to: URL(fileURLWithPath: item.out))
}

let inputData = FileHandle.standardInput.readDataToEndOfFile()
let items = try! JSONDecoder().decode([Item].self, from: inputData)
for item in items { render(item) }
print("rendered \(items.count) images")
