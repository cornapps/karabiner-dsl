module.exports = grammar({
  name: "karabiner_dsl",

  extras: $ => [/[ \t\f]+/],

  word: $ => $.identifier,

  rules: {
    source_file: $ => repeat(choice(
      $.comment_line,
      $.blank_line,
      $.statement,
    )),

    blank_line: $ => $._newline,

    comment_line: $ => seq($.comment, $._newline),

    statement: $ => seq(
      choice(
        $.section_header,
        $.condition_header,
        $.device_declaration,
        $.property_declaration,
        $.assignment,
        $.dollar_tap_mapping,
        $.tap_hold_mapping,
        $.simultaneous_mapping,
        $.hyper_combo_mapping,
        $.mapping,
        $.basic_action,
        $.key_group_command,
        $.bare_command,
      ),
      optional($.comment),
      $._newline,
    ),

    section_header: $ => choice(
      $.simple_section_header,
      $.dollar_layer_header,
      $.combo_layer_header,
      $.hold_layer_header,
      $.mode_header,
      $.numpad_header,
      $.basic_header,
      $.generic_section_header,
    ),

    simple_section_header: $ => seq(
      field("name", alias(choice(
        "devices",
        "keys",
        "rows",
        "base",
        "default",
      ), $.section_name)),
      ":",
    ),

    dollar_layer_header: $ => choice(
      seq("$", ":"),
      seq("$", ":", field("name", $.identifier)),
    ),

    combo_layer_header: $ => prec(2, seq(
      field("key", $.key_name),
      "+",
      field("name", $.identifier),
      ":",
    )),

    hold_layer_header: $ => seq(
      "hold",
      field("key", $.key_combo),
      "as",
      field("name", $.identifier),
      ":",
    ),

    mode_header: $ => seq(
      "mode",
      field("name", alias($.identifier, $.mode_name)),
      ":",
    ),

    numpad_header: $ => seq(
      "numpad",
      field("name", $.identifier),
      ":",
    ),

    basic_header: $ => seq(
      "basic",
      field("key", $.key_combo),
      ":",
    ),

    generic_section_header: $ => seq(
      field("name", $.key_combo),
      ":",
    ),

    condition_header: $ => seq(
      "when",
      choice(
        seq("app", field("value", $.condition_tail)),
        seq("device", field("value", $.condition_tail)),
        seq(
          field("variable", $.variable),
          optional(seq(field("operator", $.condition_operator), field("value", $.condition_tail))),
        ),
      ),
      optional(":"),
    ),

    condition_operator: $ => choice("is", "isnt", "=", "!="),

    device_declaration: $ => seq(
      "device",
      choice($.device_id, $.identifier),
      repeat(choice(
        $.device_id,
        $.identifier,
        "keyboard",
        "keyboard+pointing",
        "enabled",
        "swaps",
        "swap",
      )),
    ),

    property_declaration: $ => choice(
      seq("id", $.device_id),
      seq("kind", $.target_tail),
      "enabled",
      seq(choice("swaps", "swap"), $.identifier, $.identifier),
      seq("hud", choice("on", "off")),
      seq("hints", choice("on", "off")),
      seq("hyper", field("value", $.target_tail)),
      seq(choice("$", $.identifier), "is", field("value", $.target_tail)),
    ),

    assignment: $ => seq(
      field("name", $.identifier),
      "=",
      field("value", $.target_tail),
    ),

    dollar_tap_mapping: $ => seq(
      "$",
      "tap",
      field("verb", choice("sends", "opens", "runs")),
      optional(field("target", $.target_tail)),
    ),

    tap_hold_mapping: $ => seq(
      field("from", $.key_combo),
      choice("tap", "hold"),
      field("verb", choice("sends", "opens", "runs")),
      optional(field("target", $.target_tail)),
    ),

    simultaneous_mapping: $ => seq(
      field("from", $.key_combo),
      "together",
      field("verb", choice("sends", "opens", "runs")),
      optional(field("target", $.target_tail)),
    ),

    hyper_combo_mapping: $ => seq(
      "$",
      "+",
      field("from", $.key_combo),
      field("verb", choice("sends", "opens", "runs")),
      optional(field("target", $.target_tail)),
    ),

    mapping: $ => seq(
      field("from", $.key_combo),
      field("verb", $.mapping_verb),
      optional(field("target", $.target_tail)),
    ),

    mapping_verb: $ => choice(
      "sends",
      "opens",
      "runs",
      "enters",
      "exits",
      "clears",
      "commits",
      "backtracks",
      "backspaces",
      "types",
      "clicks",
      "selects",
      "resets",
      "tracks",
      "track",
      "right-clicks",
      "left-clicks",
      "triggers",
      "enables",
      "disables",
      "holds",
      "set",
      "set_variable",
      "wait",
      "delay",
      "scrolls",
      "moves",
    ),

    basic_action: $ => seq(
      optional($.action_prefix),
      field("verb", $.action_verb),
      optional(field("target", $.action_tail)),
    ),

    action_prefix: $ => choice(
      "alone",
      "held",
      seq("after", "up"),
      seq("if", choice("canceled", "invoked")),
    ),

    action_verb: $ => choice(
      "mandatory",
      "optional",
      "sends",
      "opens",
      "runs",
      "set",
      "set_variable",
      "wait",
      "delay",
      "scrolls",
      "timeout",
      "threshold",
      "track",
      "tracks",
      "clicks",
      "right-clicks",
      "left-clicks",
    ),

    key_group_command: $ => seq(
      "keys",
      field("keys", $.target_tail),
    ),

    bare_command: $ => prec(-1, $.raw_text),

    key_combo: $ => prec.left(seq(
      $.key_name,
      repeat(seq("+", $.key_name)),
    )),

    key_name: $ => choice($.variable, $.identifier),

    identifier: $ => /[A-Za-z0-9_.*\\/-][A-Za-z0-9_.*\\/-]*/,

    variable: $ => /@[A-Za-z_][A-Za-z0-9_.-]*/,

    device_id: $ => /\d+:\d+/,

    duration: $ => /\d+ms/,

    string: $ => /"([^"\\]|\\.)*"/,

    raw_text: $ => token(prec(-10, /[^\n#]+/)),

    condition_tail: $ => repeat1(choice($.string, $.duration, $.device_id, $.variable, $.identifier, ",", "+")),

    target_tail: $ => repeat1(choice($.string, $.duration, $.device_id, $.variable, $.identifier, "$", "+", "-", ",", "..")),

    action_tail: $ => repeat1(choice($.string, $.duration, $.device_id, $.variable, $.identifier, "$", "+", "-", ",", "..")),

    comment: $ => token(seq("#", /[^\n]*/)),

    _newline: $ => /\r?\n/,
  },
});
