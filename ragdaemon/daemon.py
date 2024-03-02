import json
from pathlib import Path

import networkx as nx

from ragdaemon.annotators import Hierarchy, Chunker, LayoutHierarchy
from ragdaemon.database import get_db, query_graph
from ragdaemon.llm import token_counter


class Daemon:
    """Build and maintain a searchable knowledge graph of codebase."""

    def __init__(self, cwd: Path):
        self.cwd = cwd

        # Load or setup db
        count = get_db(Path(self.cwd)).count()
        print(f"Initialized database with {count} records.")

        # Load or initialize graph
        self.graph_path = self.cwd / ".ragdaemon" / "graph.json"
        self.graph_path.parent.mkdir(exist_ok=True)
        if self.graph_path.exists():
            self.load()
        else:
            self.graph = nx.MultiDiGraph()
            self.graph.graph["cwd"] = str(cwd)
            print(f"Initialized empty graph.")

        self.pipeline = [
            Hierarchy(),
            Chunker(),
            LayoutHierarchy(),
        ]

    def save(self):
        """Saves the graph to disk."""
        data = nx.readwrite.json_graph.node_link_data(self.graph)
        with open(self.graph_path, "w") as f:
            json.dump(data, f, indent=4)
        print(f"refreshed knowledge graph saved to {self.graph_path}")

    def load(self):
        """Load the graph from disk."""
        with open(self.graph_path, "r") as f:
            data = json.load(f)
            self.graph = nx.readwrite.json_graph.node_link_graph(data)
                
    async def refresh(self):
        """Iteratively build the knowledge graph"""
        _graph = self.graph.copy()
        self.graph.graph["refreshing"] = True
        for annotator in self.pipeline:
            if not annotator.is_complete(_graph):
                _graph = await annotator.annotate(_graph)
        self.graph = _graph
        self.save()

    def search(self, query: str) -> list[dict]:
        """Return a sorted list of nodes that match the query."""
        return query_graph(query, self.graph)
    
    def render_context_message(self, context: dict[str, dict]) -> str:
        """Return a formatted context message for the given nodes."""
        output = ""
        for data in context.values():
            if output:
                output += "\n"
            tags = "" if "tags" not in data else f" ({', '.join(data['tags'])})"
            output += f"{data['id']}{tags}\n"

            file_lines = data["document"].splitlines()
            last_rendered = 0
            for line in sorted(data["lines"]):
                if line - last_rendered > 1:
                    output += "...\n"
                output += f"{line}: {file_lines[line]}\n"
                last_rendered = line
        return output

    def get_context_message(
        self, 
        query: str, 
        include: list[str] = [], 
        max_tokens: int = 8000, 
        auto_tokens: int = 2000,
    ) -> str:
        """
        Args:
            query: The search query to match context for
            include: List of node refs (path/to/file:line_start-line_end) to include automatically
            max_tokens: The maximum number of tokens for the context message
            auto_tokens: Auto-selected nodes to add in addition to include        
        """
        context = {}
        for id in include:
            path, lines_ref = id, None
            if ":" in id:
                path, lines_ref = id.split(":", 1)
            if path not in self.graph:
                print(f"Warning: no matching message found for {id}.")
                continue
            if path not in context:
                checksum = self.graph.nodes[path]["checksum"]
                message = {
                    "id": id,
                    "lines": set(), 
                    "tags": set(),
                    "document": get_db(self.cwd).get(checksum)["documents"][0],
                }
                context[path] = message
            context[path]["tags"].append("user-included")
            if lines_ref:
                for _range in lines_ref.split(","):
                    if "-" in _range:
                        start, end = _range.split("-")
                        for i in range(int(start), int(end) + 1):
                            context[path]["lines"].add(i)
                    else:
                        context[path]["lines"].add(int(_range))
            else:
                for i in range(1, len(context[path]["document"].splitlines())): 
                    context[path]["lines"].add(i)  # +1 line for filename, -1 for indexing
            
        include_context_message = self.render_context_message(context)
        include_tokens = token_counter(include_context_message)
        if include_tokens >= max_tokens:
            return include_context_message

        full_context_message = include_context_message
        auto_tokens = min(auto_tokens, max_tokens - include_tokens)
        results = self.search(query)
        for i, node in enumerate(results):
            path, lines_ref = node["path"], None
            if ":" in path:
                path, lines_ref = path.split(":", 1)
            if path not in context:
                message = {
                    "id": node["id"],
                    "lines": set(), 
                    "tags": set(), 
                    "document": node["document"]}
                message["document"] = node["document"]
                context[path] = message
            context[path]["tags"].add(f"no-{i+1}-search-result")
            if lines_ref:
                for _range in lines_ref.split(","):
                    if "-" in _range:
                        start, end = _range.split("-")
                        for i in range(int(start), int(end) + 1):
                            context[path]["lines"].add(i)
                    else:
                        context[path]["lines"].add(int(_range))
            else:
                for i in range(1, len(context[path]["document"].splitlines())): 
                    context[path]["lines"].add(i)

            next_context_message = self.render_context_message(context)
            next_tokens = token_counter(next_context_message)
            if (next_tokens - include_tokens) > auto_tokens:
                return full_context_message
            full_context_message = next_context_message
        return full_context_message
