-- inline-code.lua
-- Replace inline Code elements with Span elements carrying a custom-style.
-- Thin spaces are added at both ends to provide visual padding; the
-- InlineCode character style's shading covers them and its border wraps
-- around them, so no unshaded gap remains between glyphs and border.

function Code(el)
  local thin = utf8.char(0x202F)  -- THIN SPACE
  local padded = thin .. thin .. el.text .. thin .. thin
  return pandoc.Span(pandoc.Str(padded), {
    ['custom-style'] = 'InlineCode',
  })
end