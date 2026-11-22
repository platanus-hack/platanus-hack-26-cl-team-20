// Lista TODAS las ventanas visibles que tocan el T749 (bounds por argumento), con dueño y PID. Solo lectura.
import CoreGraphics
import Foundation
let a = CommandLine.arguments.dropFirst().compactMap(Double.init)
let t = CGRect(x: a[0], y: a[1], width: a[2], height: a[3])
let infos = (CGWindowListCopyWindowInfo([.optionOnScreenOnly], kCGNullWindowID) as? [[String: Any]]) ?? []
for i in infos {
    guard let bd = i[kCGWindowBounds as String] as? [String: Any], let r = CGRect(dictionaryRepresentation: bd as CFDictionary),
          r.intersects(t), r.width > 2 else { continue }
    print("pid=\(i[kCGWindowOwnerPID as String]!) dueño=\(i[kCGWindowOwnerName as String] ?? "?") ventana=\(i[kCGWindowNumber as String]!) bounds=(\(Int(r.origin.x)),\(Int(r.origin.y)),\(Int(r.width))x\(Int(r.height))) capa=\(i[kCGWindowLayer as String]!)")
}
