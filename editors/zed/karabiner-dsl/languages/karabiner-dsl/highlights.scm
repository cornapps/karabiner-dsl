(comment) @comment
(string) @string
(duration) @number
(device_id) @number
(variable) @variable.special

(section_name) @keyword
(mode_name) @type
(condition_operator) @operator
(mapping_verb) @function
(action_verb) @function

[
  "when"
  "app"
  "device"
  "numpad"
  "mode"
  "basic"
  "hold"
  "as"
  "id"
  "kind"
  "enabled"
  "swaps"
  "swap"
  "hud"
  "hints"
  "hyper"
  "is"
  "on"
  "off"
  "tap"
  "together"
  "mandatory"
  "optional"
  "alone"
  "held"
  "after"
  "if"
  "canceled"
  "invoked"
] @keyword

[
  "+"
  "="
  ":"
  "$"
] @operator

(key_combo (key_name (identifier) @variable.parameter))
(assignment name: (identifier) @property)
(condition_tail) @string.special
(action_tail) @string.special
(target_tail) @string.special

((identifier) @constant.builtin
  (#any-of? @constant.builtin
    "t9"
    "neru"
    "left"
    "right"
    "middle"
    "left_click"
    "right_click"
    "middle_click"
    "then"
    "current"
    "center"
    "through"
    "return"
    "lazy"
    "halt"
    "until"
    "key"
    "up"
    "pointer"))
