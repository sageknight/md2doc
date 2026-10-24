-- task-list-remove-bullet.lua
-- Converts Pandoc task lists (BulletList containing ☐/☒/✅/❌ items)
-- into a sequence of paragraphs with a custom style that has no bullet.

local checkbox_chars = { '☐', '☒', '✅', '❌', '✓', '✗' }  -- add more if needed

local function starts_with_checkbox(inline)
  if inline.t ~= 'Str' then return false end
  for _, ch in ipairs(checkbox_chars) do
    if inline.text:sub(1, #ch) == ch then
      return true
    end
  end
  return false
end

function BulletList(el)
  local is_task_list = false

  -- Check whether at least one item begins with a checkbox.
  for _, item in ipairs(el.content) do
    local first_block = item[1]
    if first_block and first_block.t == 'Plain' then
      local first_inline = first_block.content[1]
      if first_inline and starts_with_checkbox(first_inline) then
        is_task_list = true
        break
      end
    end
  end

  if not is_task_list then
    return nil  -- leave ordinary bullet lists alone
  end

  -- Convert each item into a Para and wrap everything in a Div
  -- whose custom-style is "TaskList".
  local blocks = {}
  for _, item in ipairs(el.content) do
    for _, block in ipairs(item) do
      if block.t == 'Plain' then
        local content = block.content
        -- Insert a tab after the checkbox so the text starts at the
        -- hanging‑indent position defined in the TaskList style.
        if #content > 0 and starts_with_checkbox(content[1]) then
          table.insert(content, 2, pandoc.RawInline('opendocument', '<text:tab/>'))
        end
        blocks[#blocks + 1] = pandoc.Para(content)
      else
        blocks[#blocks + 1] = block
      end
    end
  end

  return pandoc.Div(blocks, { ['custom-style'] = 'TaskList' })
end