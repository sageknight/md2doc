local combined_style = 'DefinitionListCombined'
local body_style     = 'DefinitionListBody'

local function wrap_term_bold(term)
  -- Wrap the term inlines in Strong so the term stays bold
  -- after it is lifted out of the Definition Term paragraph style.
  return { pandoc.Strong(term) }
end

local function combine_term_and_first_block(term, blocks)
  if #blocks == 0 then
    local inlines = wrap_term_bold(term)
    return { pandoc.Div({ pandoc.Para(inlines) }, { ['custom-style'] = combined_style }) }
  end

  local first_block = blocks[1]
  local rest_blocks = {}
  for i = 2, #blocks do
    rest_blocks[#rest_blocks + 1] = blocks[i]
  end

  local combined_inlines = {}
  for _, inline in ipairs(wrap_term_bold(term)) do
    combined_inlines[#combined_inlines + 1] = inline
  end
  combined_inlines[#combined_inlines + 1] = pandoc.Str(utf8.char(0x2003))

  if first_block.t == 'Para' or first_block.t == 'Plain' then
    for _, inline in ipairs(first_block.content) do
      combined_inlines[#combined_inlines + 1] = inline
    end
  else
    rest_blocks = blocks
  end

  local result = {}
  result[#result + 1] = pandoc.Div(
    { pandoc.Para(combined_inlines) },
    { ['custom-style'] = combined_style }
  )

  for _, block in ipairs(rest_blocks) do
    result[#result + 1] = pandoc.Div(
      { block },
      { ['custom-style'] = body_style }
    )
  end

  return result
end

function DefinitionList(el)
  local new_blocks = {}
  for _, item in ipairs(el.content) do
    local term = item[1]
    local defs = item[2]

    if #defs > 0 then
      local combined = combine_term_and_first_block(term, defs[1])
      for _, b in ipairs(combined) do
        new_blocks[#new_blocks + 1] = b
      end
      for i = 2, #defs do
        for _, block in ipairs(defs[i]) do
          new_blocks[#new_blocks + 1] = pandoc.Div(
            { block },
            { ['custom-style'] = body_style }
          )
        end
      end
    else
      new_blocks[#new_blocks + 1] = pandoc.Div(
        { pandoc.Para(wrap_term_bold(term)) },
        { ['custom-style'] = combined_style }
      )
    end
  end
  return new_blocks
end