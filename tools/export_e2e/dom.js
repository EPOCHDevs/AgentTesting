// Measures the Terminal's print layout (EpochClientPortal StudyPrintPage/PrintLayout).
// Called by Playwright as `page.evaluate(source, mode)`; returns plain JSON.
async (mode) => {
  const article = document.querySelector("article[data-print-layout]")
  const text = (node) => (node ? node.textContent.replace(/\s+/g, " ").trim() : null)
  const digest = async (value) => {
    const bytes = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(value))
    return [...new Uint8Array(bytes)].map((b) => b.toString(16).padStart(2, "0")).join("")
  }

  // A unit is one KeepTogether box: the section heading (first unit only), an
  // optional sub-heading, and one block.
  const describeBlock = async (block) => {
    if (!block) return null
    const own = (selector) => [...block.children].filter((child) => child.matches(selector))
    const result = {
      tag: block.tagName.toLowerCase(),
      text: text(block)?.slice(0, 400) ?? "",
      h4: own("h4").map(text),
      ps: own("p").map(text),
    }
    if (block.tagName === "FIGURE") {
      const caption = block.querySelector("figcaption")
      const spans = caption ? [...caption.children].map(text) : []
      const img = block.querySelector("img")
      result.figure = {
        title: spans[0] ?? null,
        subtitle: spans[1] ?? null,
        legend: [...block.querySelectorAll(":scope > ul li")].map(text),
        image: img
          ? {
              alt: img.getAttribute("alt"),
              length: img.src.length,
              sha256: await digest(img.src),
              complete: img.complete,
              natural_width: img.naturalWidth,
              natural_height: img.naturalHeight,
            }
          : null,
        message: own("p").map(text).find((value) => value) ?? null,
      }
    }
    const tables = [...block.querySelectorAll("table")]
    if (tables.length) {
      result.tables = tables.map((table) => ({
        caption: text(table.querySelector("caption")),
        header_cells: table.querySelectorAll("thead th").length,
        headers: [...table.querySelectorAll("thead th")].map(text),
        body_rows: table.querySelectorAll("tbody tr").length,
        first_row: [...(table.querySelector("tbody tr")?.children ?? [])].map(text),
      }))
    }
    const dl = block.querySelectorAll("dl")
    if (dl.length) result.metrics = { lists: dl.length, labels: [...block.querySelectorAll("dt")].map(text) }
    const ul = own("ul")
    if (ul.length) result.list_items = ul.reduce((sum, list) => sum + list.querySelectorAll(":scope > li").length, 0)
    return result
  }

  const describe = async () => {
    if (!article) return { found: false }
    // The cover: a row with the logo and export date, the title and question, then the facts.
    const header = article.querySelector(":scope > header")
    const cover = header
      ? {
          title: text(header.querySelector("h1")),
          exported: text(header.querySelector(":scope > div > p")),
          facts: header.querySelector(":scope > p")?.textContent ?? null,
          question: text(header.querySelector("h1")?.parentElement?.querySelector(":scope > :not(h1)")),
        }
      : null
    const sections = []
    for (const section of article.querySelectorAll(":scope > section")) {
      const heading = section.querySelector("header")
      const units = []
      for (const unit of [...section.children].filter((child) => child.tagName === "DIV")) {
        const children = [...unit.children]
        const sub = children.find((child) => child.tagName === "DIV" && child.querySelector(":scope > h3"))
        const rest = children.filter((child) => child.tagName !== "HEADER" && child !== sub)
        units.push({
          section_heading: children.some((child) => child.tagName === "HEADER"),
          heading: sub
            ? { text: text(sub.querySelector("h3")), description: text(sub.querySelector("p")) }
            : null,
          block: await describeBlock(rest.at(-1) ?? null),
          extra: rest.length > 1 ? rest.length - 1 : 0,
        })
      }
      sections.push({
        aria_label: section.getAttribute("aria-label"),
        title: text(heading?.querySelector("h2")),
        description: text(heading?.querySelector("p")),
        units,
      })
    }
    return {
      found: true,
      cover,
      notices: [...article.querySelectorAll(":scope > p")].map(text),
      sections,
      body_ready: document.body.dataset.exportReady ?? null,
    }
  }

  // Each chart image decoded and sampled: a blank or empty PNG has (almost) one colour.
  const imageStats = async () => {
    if (!article) return []
    const results = []
    for (const [index, img] of [...article.querySelectorAll("figure img")].entries()) {
      const entry = { index, alt: img.getAttribute("alt"), natural_width: img.naturalWidth, natural_height: img.naturalHeight }
      try {
        await img.decode()
        const scale = Math.min(1, 720 / Math.max(1, img.naturalWidth))
        const width = Math.max(1, Math.round(img.naturalWidth * scale))
        const height = Math.max(1, Math.round(img.naturalHeight * scale))
        const canvas = document.createElement("canvas")
        canvas.width = width
        canvas.height = height
        const context = canvas.getContext("2d", { willReadFrequently: true })
        context.drawImage(img, 0, 0, width, height)
        const pixels = context.getImageData(0, 0, width, height).data
        const counts = new Map()
        for (let i = 0; i < pixels.length; i += 4) {
          const key =
            ((pixels[i] >> 3) << 15) | ((pixels[i + 1] >> 3) << 10) | ((pixels[i + 2] >> 3) << 5) | (pixels[i + 3] >> 3)
          counts.set(key, (counts.get(key) ?? 0) + 1)
        }
        const total = width * height
        const background = Math.max(...counts.values())
        entry.decoded = true
        entry.sampled = [width, height]
        entry.distinct_colors = counts.size
        entry.non_background = Number((1 - background / total).toFixed(5))
      } catch (error) {
        entry.decoded = false
        entry.error = String(error).slice(0, 200)
      }
      results.push(entry)
    }
    return results
  }

  // Words a cell breaks across lines other than after a hyphen, slash, comma or a
  // thousands separator's break point: "Succes|s", "N|o|v", "32.3|%".
  const SOFT_BREAK = /[-\u2013\u2014/,]/
  const brokenWords = (cell) => {
    const found = []
    const walker = document.createTreeWalker(cell, NodeFilter.SHOW_TEXT)
    for (let node = walker.nextNode(); node; node = walker.nextNode()) {
      for (const match of node.data.matchAll(/[^\s\u200b]{2,}/g)) {
        const range = document.createRange()
        range.setStart(node, match.index)
        range.setEnd(node, match.index + match[0].length)
        if ([...range.getClientRects()].filter((r) => r.width > 0).length < 2) continue
        let top = null
        const pieces = [""]
        for (let i = 0; i < match[0].length; i++) {
          range.setStart(node, match.index + i)
          range.setEnd(node, match.index + i + 1)
          const box = range.getBoundingClientRect()
          if (top !== null && box.width > 0 && box.top - top > 2 && !SOFT_BREAK.test(match[0][i - 1])) pieces.push("")
          if (box.width > 0) top = box.top
          pieces[pieces.length - 1] += match[0][i]
        }
        if (pieces.length > 1) found.push(pieces.join("|"))
      }
    }
    return found
  }

  // Under print media at a page's printable width: does any cell's content run past
  // its column, any table past the page, or any word break inside itself?
  const tableOverflow = () => {
    if (!article) return []
    const page = article.getBoundingClientRect()
    return [...article.querySelectorAll("table")].map((table, index) => {
      let owner = table.parentElement
      while (owner && owner !== article && !owner.querySelector(":scope > h4")) owner = owner.parentElement
      const rect = table.getBoundingClientRect()
      const cells = []
      const broken = []
      for (const cell of table.querySelectorAll("th, td")) {
        for (const word of brokenWords(cell))
          broken.push({ row: cell.parentElement.rowIndex, column: cell.cellIndex, word })
        const box = cell.getBoundingClientRect()
        let widest = cell.scrollWidth - cell.clientWidth
        for (const child of cell.querySelectorAll("*")) {
          const inner = child.getBoundingClientRect()
          widest = Math.max(widest, inner.right - box.right, box.left - inner.left)
        }
        if (widest > 1)
          cells.push({
            row: cell.parentElement.rowIndex,
            column: cell.cellIndex,
            text: text(cell).slice(0, 80),
            overflow_px: Math.round(widest),
          })
      }
      const wrapper = table.parentElement
      return {
        index,
        title: text(owner?.querySelector(":scope > h4")) ?? null,
        columns: table.querySelectorAll("thead th").length,
        table_width: Math.round(rect.width),
        page_width: Math.round(page.width),
        past_page_px: Math.round(Math.max(rect.right - page.right, page.left - rect.left, 0)),
        clipped_px: Math.max(0, table.scrollWidth - wrapper.clientWidth),
        overflowing_cells: cells.length,
        examples: cells.slice(0, 5),
        broken_words: broken.length,
        broken_examples: broken.slice(0, 8),
      }
    })
  }

  if (mode === "describe") return describe()
  if (mode === "imageStats") return imageStats()
  if (mode === "tableOverflow") return tableOverflow()
  throw new Error(`Unknown mode ${mode}`)
}
