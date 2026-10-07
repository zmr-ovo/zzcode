import AppKit

struct TerminalScreenshot {
    let filename: String
    let title: String
    let lines: [String]
    let width: CGFloat
    let height: CGFloat
}

let outputRoot = URL(fileURLWithPath: "assets/screenshots", isDirectory: true)
let background = NSColor(calibratedRed: 0.055, green: 0.086, blue: 0.145, alpha: 1)
let titleBackground = NSColor(calibratedRed: 0.112, green: 0.157, blue: 0.220, alpha: 1)
let border = NSColor(calibratedRed: 0.129, green: 0.204, blue: 0.337, alpha: 1)
let foreground = NSColor(calibratedRed: 0.790, green: 0.812, blue: 0.845, alpha: 1)
let font = NSFont(name: "Menlo", size: 22) ?? NSFont.monospacedSystemFont(ofSize: 22, weight: .regular)

let helpLines = [
    "usage: zzcode [-h] [--cwd CWD] [--provider {ollama,openai,anthropic}]",
    "              [--model MODEL] [--host HOST] [--base-url BASE_URL]",
    "              [--ollama-timeout OLLAMA_TIMEOUT] [--openai-timeout OPENAI_TIMEOUT]",
    "              [--resume RESUME] [--approval {ask,auto,never}]",
    "              [--secret-env-name SECRET_ENV_NAMES] [--max-steps MAX_STEPS]",
    "              [--max-new-tokens MAX_NEW_TOKENS] [--temperature TEMPERATURE]",
    "              [--top-p TOP_P]",
    "              [prompt ...]",
    "",
    "Minimal coding agent for Ollama, OpenAI-compatible, or Anthropic-compatible",
    "models.",
    "",
    "positional arguments:",
    "  prompt                   Optional one-shot prompt. (default: None)",
    "",
    "options:",
    "  -h, --help               show this help message and exit",
    "  --cwd CWD                Workspace directory. (default: .)",
    "  --provider {ollama,openai,anthropic}",
    "                           Model backend to use. (default: openai)",
    "  --model MODEL            Model name override. Defaults to qwen3.5:4b for Ollama,",
    "                           OPENAI_MODEL for openai, and ANTHROPIC_MODEL for anthropic",
    "                           when set. (default: None)",
    "  --host HOST              Ollama server URL. (default: http://127.0.0.1:11434)",
    "  --base-url BASE_URL      Provider API base URL for openai or anthropic.",
    "                           (default: None)",
    "  --ollama-timeout OLLAMA_TIMEOUT",
    "                           Ollama request timeout in seconds. (default: 300)",
    "  --openai-timeout OPENAI_TIMEOUT",
    "                           OpenAI-compatible request timeout in seconds. (default: 300)",
    "  --resume RESUME          Session id to resume or 'latest'. (default: None)",
    "  --approval {ask,auto,never}",
    "                           Approval policy for risky tools. (default: ask)",
    "  --secret-env-name SECRET_ENV_NAMES",
    "                           Extra environment variable names to treat as secrets for",
    "                           trace/report redaction. (default: [])",
    "  --max-steps MAX_STEPS    Maximum tool/model iterations per request. (default: 6)",
    "  --max-new-tokens MAX_NEW_TOKENS",
    "                           Maximum model output tokens per step. (default: 512)",
    "  --temperature TEMPERATURE",
    "                           Sampling temperature sent to Ollama. (default: 0.2)",
    "  --top-p TOP_P            Top-p sampling value sent to Ollama. (default: 0.9)",
]

let welcomeLines = [
    "+======================================================================================+",
    "|                                      /\\___/\\                                      |",
    "|                                     (  o o  )                                      |",
    "|                                     /   ^   \\                                     |",
    "|                                    /|       |\\                                    |",
    "|                                       zzcode                                       |",
    "|                                local coding agent                                  |",
    "|                             calm shell, ready for work                             |",
    "+--------------------------------------------------------------------------------------+",
    "|                                                                                      |",
    "| WORKSPACE  /Users/martinlos/code/zzcode                                             |",
    "| MODEL      gpt-5.4                              BRANCH     main                      |",
    "| APPROVAL   ask                                  SESSION    20260409-145245-1f8a43    |",
    "|                                                                                      |",
    "+======================================================================================+",
    "",
    "zzcode>",
]

let replLines = Array(welcomeLines.dropLast()) + [
    "zzcode> Commands:",
    "/help    Show this help message.",
    "/memory  Show the agent's distilled working memory.",
    "/session Show the path to the saved session file.",
    "/reset   Clear the current session history and memory.",
    "/exit    Exit the agent.",
    "",
    "zzcode> /Users/martinlos/code/zzcode/.zzcode/sessions/20260409-145245-4e8666.json",
    "",
    "zzcode>",
]

let screenshots = [
    TerminalScreenshot(
        filename: "zzcode-help.png",
        title: "real terminal: uv run zzcode --help",
        lines: helpLines,
        width: 1308,
        height: 1612
    ),
    TerminalScreenshot(
        filename: "zzcode-start.png",
        title: "real terminal: uv run zzcode",
        lines: welcomeLines,
        width: 1336,
        height: 696
    ),
    TerminalScreenshot(
        filename: "zzcode-repl.png",
        title: "real terminal: /help and /session",
        lines: replLines,
        width: 1336,
        height: 996
    ),
]

func render(_ screenshot: TerminalScreenshot) throws {
    let image = NSImage(size: NSSize(width: screenshot.width, height: screenshot.height))
    image.lockFocus()

    let outerRect = NSRect(x: 8, y: 8, width: screenshot.width - 16, height: screenshot.height - 16)
    let outerPath = NSBezierPath(roundedRect: outerRect, xRadius: 22, yRadius: 22)
    background.setFill()
    outerPath.fill()
    border.setStroke()
    outerPath.lineWidth = 1
    outerPath.stroke()

    NSGraphicsContext.current?.saveGraphicsState()
    outerPath.addClip()
    titleBackground.setFill()
    NSBezierPath(rect: NSRect(x: 8, y: screenshot.height - 64, width: screenshot.width - 16, height: 56)).fill()
    NSGraphicsContext.current?.restoreGraphicsState()

    let circleY = screenshot.height - 37
    for (x, color) in [(33.0, NSColor.systemRed), (57.0, NSColor.systemYellow), (81.0, NSColor.systemGreen)] {
        color.setFill()
        NSBezierPath(ovalIn: NSRect(x: x - 8, y: circleY - 8, width: 16, height: 16)).fill()
    }

    let attributes: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: foreground]
    NSString(string: screenshot.title).draw(at: NSPoint(x: 116, y: screenshot.height - 49), withAttributes: attributes)

    let lineHeight: CGFloat = 34
    var y = screenshot.height - 104
    for line in screenshot.lines {
        NSString(string: line).draw(at: NSPoint(x: 28, y: y), withAttributes: attributes)
        y -= lineHeight
    }

    image.unlockFocus()
    guard let tiff = image.tiffRepresentation,
          let bitmap = NSBitmapImageRep(data: tiff),
          let data = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "render", code: 1)
    }
    try data.write(to: outputRoot.appendingPathComponent(screenshot.filename))
}

for screenshot in screenshots {
    try render(screenshot)
}
