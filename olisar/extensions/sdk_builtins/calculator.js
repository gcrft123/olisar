// Built-in SDK extension: calculator. A small recursive-descent evaluator (no eval)
// over + - * / % ** and parentheses — proves the SDK can host real logic in-sandbox.
defineExtension({
  id: "calculator",
  name: "Calculator",
  version: "1.0.0",
  category: "Utility",
  description: "Olisar can do exact arithmetic instead of guessing at numbers.",
  permissions: [],
  tools: [{
    name: "calculate",
    description:
      "Evaluate a plain arithmetic expression exactly (+, -, *, /, %, **, " +
      "parentheses). Use for any math so you don't miscompute.",
    parameters: {
      type: "object",
      properties: { expression: { type: "string", description: "an arithmetic expression" } },
      required: ["expression"],
    },
    handler: function (args) {
      var s = String(args.expression || "").trim();
      var p = 0;
      function peek() { while (s[p] === " ") p++; return s[p]; }
      function atom() {
        var c = peek();
        if (c === "(") { p++; var v = expr(); if (peek() !== ")") throw 0; p++; return v; }
        if (c === "-") { p++; return -atom(); }
        if (c === "+") { p++; return atom(); }
        var start = p;
        while (p < s.length && /[0-9.]/.test(s[p])) p++;
        if (p === start) throw 0;
        return parseFloat(s.slice(start, p));
      }
      function power() { var v = atom(); if (peek() === "*" && s[p + 1] === "*") { p += 2; return Math.pow(v, power()); } return v; }
      function term() {
        var v = power();
        for (;;) {
          var c = peek();
          if (c === "*" && s[p + 1] !== "*") { p++; v *= power(); }
          else if (c === "/") { p++; v /= power(); }
          else if (c === "%") { p++; v %= power(); }
          else return v;
        }
      }
      function expr() {
        var v = term();
        for (;;) {
          var c = peek();
          if (c === "+") { p++; v += term(); }
          else if (c === "-") { p++; v -= term(); }
          else return v;
        }
      }
      try {
        var result = expr();
        if (peek() !== undefined) throw 0;
        if (!isFinite(result)) throw 0;
        if (result === Math.floor(result)) result = Math.floor(result);
        return s + " = " + result;
      } catch (e) {
        return "I couldn't compute " + JSON.stringify(s) +
          " — I only do plain arithmetic (+ - * / % ** and parentheses).";
      }
    },
  }],
});
