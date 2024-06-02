import ast

from spice import Spice, SpiceMessages

from ragdaemon.graph import KnowledgeGraph


system_prompt = """\
Return a Python script inside ```triple backticks```, and nothing else.
You are part of an automated coding assistant called Ragdaemon. 
You iteratively write Python scripts and review their output, and then return an answer to the user. 
You have access to three global variables:
- `graph`: A networkx.MultiDiGraph object representing a codebase.
- `print`: An override of Python's default print function that appends to your conversation.
- `answer`: A function that returns text to the user.

A typical interaction goes like this:
1. The user asks a question
2. You write a script to search the `graph` for some files or keywords from the question and `print()` the output
3. The compiler runs your scripts and adds the output to the conversation
[repeat 2 and 3 until you have found the answer]
4. You compose a clear, concise answer to the user's question based on what you see in the conversation
5. You write a script that with `answer(<your answer here>)` to communicate the answer to the user

# Graph Structure
### Nodes
Directories, files, chunks (functions, classes or methods) and diffs. They have attributes:
- `id`: Human-readable path, e.g. `path/to/file:class.method`
- `type`: One of "directory", "file", "chunk", "diff"
- `document`: The content of the node. For files, diffs and chunks, it's the text. For directories, it's a list of files.

### Edges
Have a `type` attribute which is either:
- `hierarchy`: Point from parent to child: directory to files, file to chunks, etc.
- `diff`: Point from a diff root node to diff chunks, and diff chunks to the file/chunks they modify.

### Notes
- Code files may or may not include chunks. If they do:
    - The `path/to/file` has exactly one child, `path/to/file:BASE` (the BASE chunk)
    - Top-level functions or classes are children of the BASE chunk. Methods or sub-functions are then children of those.
    - Every line of code is contained in exactly one chunk. The BASE chunk acts as a catch-all for imports and space between functions/classes.

# Instructions
Review the user query and the full conversation history.
If additional information is needed:
    1. Write a Python SCRIPT, inside ```triple backticks``` that identifies the next piece of information needed, either using the graph or other Python code, like `subprocess`.
    2. Save the relevant output using the `print()` function.
If you have the answer:
    1. Write a Python SCRIPT, inside ``triple backticks``` that communicates back to the user using the `answer()` function.

# Remember:
* ONLY RETURN PYTHON CODE INSIDE TRIPLE BACKTICKS. Anything else will be ignored.
* Don't communicate to the user until you've gathered the necessary context, as calling `answer()` will end the conversation.
* Use the `graph` for inspecting codebase structure and content.
* If you need to run a shell commands, like git, use the `subprocess` module.
* Avoid preamble or narration of what you're doing. Just give the user a concise answer to their question.
* You can include programmatic logic in your answer, e.g. by using `answer` inside a `for` loop or `if` block.
* ONLY COMMUNICATE TO THE USER VIA PYTHON SCRIPT WITH THE `answer()` FUNCTION. Anything else will be ignored.

# Example
--------------------------------------------------------------------------------
USER: "What is does get_document do?"
CONVERSATION: []

SCRIPT:
```
print([node for node in graph.nodes if "get_document" in node])
```
--------------------------------------------------------------------------------
USER: "What is does get_document do?"
CONVERSATION: [
    "Script: print([node for node in graph.nodes if 'get_document' in node])\nOutput: ['get_document']\n"
    Output: ['src/main.py:get_document']"
]

SCRIPT:
```
print(graph.nodes["src/main.py:get_document"]["document"])
```
--------------------------------------------------------------------------------
USER: "What is does get_document do?"
CONVERSATION: [
    "Script: print([node for node in graph.nodes if 'get_document' in node])\nOutput: ['get_document']\n"
    Output: ['src/main.py:get_document']",
    "Script: print(graph.nodes['src/main.py:get_document']['document'])
    Output: 'def get_document(...): ...'\n"
]

SCRIPT:
```
answer("The get_document function in `src/main.py` returns the content of a file by...")
```
"""


def parse_script(response: str) -> tuple[str, str]:
    """Split the response into a message and a script.

    Expected use is: run the script if there is one, otherwise print the message.
    """
    # Parse delimiter
    n_delimiters = response.count("```")
    if n_delimiters < 2:
        return response, ""
    segments = response.split("```")
    message = f"{segments[0]}\n{segments[-1]}"
    script = "```".join(segments[1:-1]).strip()  # Leave 'inner' delimiters alone

    # Check for common mistakes
    if script.split("\n")[0].startswith("python"):
        script = "\n".join(script.split("\n")[1:])
    try:  # Make sure it's valid python
        ast.parse(script)
    except SyntaxError:
        raise SyntaxError(f"Script contains invalid Python:\n{response}")
    return message, script


class Printer:
    printed: str = ""
    answered: str = ""

    def print(self, *args: str):
        self.printed += " ".join(str(a) for a in args) + "\n"

    def answer(self, *args: str):
        self.answered += " ".join(str(a) for a in args) + "\n"


async def cerebrus(
    query: str, graph: KnowledgeGraph, spice_client: Spice, leash: bool = False
) -> str:
    messages = SpiceMessages(spice_client)
    messages.add_system_message(system_prompt)
    messages.add_user_message(query)

    starting_cost = spice_client.total_cost
    printer = Printer()
    max_iterations = 10
    answer = ""
    for _ in range(max_iterations):
        response = await spice_client.get_response(messages=messages)
        script = ""
        try:
            message, script = parse_script(response.text)
            if not script:
                if message:
                    answer = message
                break
            exec(
                script,
                {"print": printer.print, "answer": printer.answer, "graph": graph},
            )
        except KeyboardInterrupt:
            raise
        except Exception as e:
            printer.print(f"Error: {e}")
        if not printer.printed:
            if printer.answered:
                answer = printer.answered
            break
        next_message = f"Script: {script}\n{80*'-'}\nOutput: {printer.printed}"
        messages.add_system_message(next_message)
        if leash:
            print(next_message)
        printer.printed = ""
    if leash:
        print("Total cost:", spice_client.total_cost - starting_cost)
    return answer
