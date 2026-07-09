# InvalidToolRegistration

A value passed to `Runtime(tools=[...])` isn't a `Tool` instance.
The most common cause is forgetting the `@tool` decorator and passing
the bare function. Fires at Runtime construction; the check fails
fast before any behavior runs.

Multi-inherits `TypeError` for back-compat — code that catches
`TypeError` around runtime construction continues to work.

## Quick fix

Decorate the function with `@tool`, and pass the decorated object:

```python
from activegraph.tools import tool

@tool(name="my_tool", input_schema=MyInput, output_schema=MyOutput)
def my_tool(args, ctx):
    ...

rt = Runtime(graph, tools=[my_tool])
```

If the function was already decorated, confirm you're passing the
decorator's return value (the wrapped `Tool`), not the original
function. A common mistake is passing the unwrapped name from a
module that exports both:

```python
# Wrong: passing the bare function
from my_tools_module import my_tool_function   # not the @tool object
rt = Runtime(graph, tools=[my_tool_function])

# Right: passing the @tool-wrapped Tool instance
from my_tools_module import my_tool   # the @tool-decorated symbol
rt = Runtime(graph, tools=[my_tool])
```

## How to diagnose

The error names the offending value and its type:

```
InvalidToolRegistration: tool registration value is not a Tool
instance (got function)

What failed:
  Runtime(tools=[...]) was given a value that isn't a Tool instance:
    value: <function bare_function at 0x...>
    type:  function
```

From code:

```python
try:
    rt = Runtime(graph, tools=[some_value])
except InvalidToolRegistration as e:
    print(e.value)              # the offending value
    print(type(e.value).__name__)
```

If the type is `function`, the most likely cause is the missing
`@tool` decorator. If the type is something else (a `dict`, a
class, a `Protocol`), something deeper is wrong with the value
you're passing.

## When does this fire

At `Runtime(...)` construction, while building the tool registry.
Each value in `tools=[...]` is checked individually; the first
non-`Tool` value triggers the error and the rest aren't checked
(the runtime construction can't proceed).

## Why the framework refuses to continue

The `Tool` wrapper carries the tool's declared name, input schema,
output schema, timeout, and deterministic flag. Registering a bare
function would skip those declarations and the runtime could not
validate calls into the tool — schema-violating calls would reach
the body and produce wrong-shape data. The check fails fast at
construction.

See [`failure-model`](../../concepts/failure-model.md) for the
broader principle.

## What's related

- [`missing-tool-error`](missing-tool-error.md) — fires when an
  `@llm_behavior` declares a tool name that isn't in the registry.
  Different shape: declared name missing vs. wrong-type value
  registered.
- [Tools API reference](../api/tools.md) — the canonical
  reference for the `@tool` decorator and the Tool wrapper.
