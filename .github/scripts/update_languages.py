import os
import json
import math
import subprocess
import urllib.request
import urllib.error

GRAPHQL_OWNED_QUERY = """
query {
  viewer {
    repositories(first: 100, isFork: false, affiliations: [OWNER]) {
      nodes {
        name
        languages(first: 10, orderBy: {field: SIZE, direction: DESC}) {
          edges {
            size
            node {
              name
              color
            }
          }
        }
      }
    }
  }
}
"""

GRAPHQL_COLLAB_QUERY = """
query {
  viewer {
    repositories(first: 100, isFork: false, affiliations: [COLLABORATOR]) {
      nodes {
        nameWithOwner
        defaultBranchRef {
          name
        }
      }
    }
  }
}
"""

EXCLUDE_REPOS = {"CSARCH2-Case-Study-1-Integer-Machine"}

EXT_MAP = {
    ".ts": ("TypeScript", "#3178c6"),
    ".tsx": ("TypeScript", "#3178c6"),
    ".js": ("JavaScript", "#f1e05a"),
    ".jsx": ("JavaScript", "#f1e05a"),
    ".py": ("Python", "#3572A5"),
    ".java": ("Java", "#b07219"),
    ".kt": ("Kotlin", "#A97BFF"),
    ".kts": ("Kotlin", "#A97BFF"),
    ".c": ("C", "#555555"),
    ".h": ("C", "#555555"),
    ".asm": ("Assembly", "#6E4C13"),
    ".s": ("Assembly", "#6E4C13"),
    ".rs": ("Rust", "#dea584"),
    ".css": ("CSS", "#663399"),
    ".html": ("HTML", "#e34c26"),
    ".r": ("R", "#198CE7"),
    ".mdx": ("MDX", "#fcb32c")
}

def get_token():
    return os.environ.get("GITHUB_TOKEN") or os.environ.get("PAT_TOKEN")

def fetch_graphql(query):
    token = get_token()
    if token:
        req = urllib.request.Request(
            "https://api.github.com/graphql",
            data=json.dumps({"query": query}).encode("utf-8"),
            headers={
                "Authorization": f"bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "GitHub-Languages-Stats-Bot"
            }
        )
        with urllib.request.urlopen(req) as response:
            return json.loads(response.read().decode("utf-8"))
    else:
        cmd = ["gh", "api", "graphql", "-f", f"query={query}"]
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8")
        return json.loads(res.stdout)

def fetch_rest(endpoint):
    token = get_token()
    if token:
        req = urllib.request.Request(
            f"https://api.github.com/{endpoint.lstrip('/')}",
            headers={
                "Authorization": f"bearer {token}",
                "Accept": "application/vnd.github.v3+json",
                "User-Agent": "GitHub-Languages-Stats-Bot"
            }
        )
        try:
            with urllib.request.urlopen(req) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            print(f"HTTP Error {e.code} for {endpoint}: {e.reason}")
            return None
    else:
        cmd = ["gh", "api", endpoint]
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
        if res.returncode == 0:
            return json.loads(res.stdout)
        return None

def main():
    print("1. Fetching owned repositories from GitHub...")
    owned_data = fetch_graphql(GRAPHQL_OWNED_QUERY)

    languages = {}
    total_bytes = 0

    for repo in owned_data.get("data", {}).get("viewer", {}).get("repositories", {}).get("nodes", []):
        repo_name = repo.get("name")
        if repo_name in EXCLUDE_REPOS:
            continue
        for edge in repo.get("languages", {}).get("edges", []):
            size = edge.get("size", 0)
            node = edge.get("node", {})
            name = node.get("name")
            color = node.get("color") or "#888888"
            if name:
                if name not in languages:
                    languages[name] = {"size": 0, "color": color}
                languages[name]["size"] += size
                total_bytes += size

    print("2. Fetching collaborated repositories and filtering to authored code only...")
    collab_data = fetch_graphql(GRAPHQL_COLLAB_QUERY)
    collab_nodes = collab_data.get("data", {}).get("viewer", {}).get("repositories", {}).get("nodes", [])

    for cnode in collab_nodes:
        repo_full = cnode.get("nameWithOwner")
        default_branch = (cnode.get("defaultBranchRef") or {}).get("name") or "main"
        if not repo_full:
            continue

        commits = fetch_rest(f"repos/{repo_full}/commits?author=ainere&per_page=100")
        if not commits or not isinstance(commits, list) or len(commits) == 0:
            continue

        print(f"   Analyzing {repo_full} ({len(commits)} commits by ainere)...")
        authored_files = set()
        for c in commits:
            sha = c.get("sha")
            if not sha:
                continue
            commit_detail = fetch_rest(f"repos/{repo_full}/commits/{sha}")
            if commit_detail and isinstance(commit_detail, dict):
                for f in commit_detail.get("files", []):
                    fname = f.get("filename")
                    if fname:
                        authored_files.add(fname)

        # Special co-authorship cases (e.g. LBYARCH2-MCO2 kernel.asm declared in file header)
        if repo_full == "djmarcaida/LBYARCH2-MCO2":
            authored_files.add("kernel.asm")

        # Fetch tree to get exact file sizes
        tree_data = fetch_rest(f"repos/{repo_full}/git/trees/{default_branch}?recursive=1")
        if not tree_data and default_branch != "master":
            tree_data = fetch_rest(f"repos/{repo_full}/git/trees/master?recursive=1")

        if tree_data and isinstance(tree_data, dict):
            tree = tree_data.get("tree", [])
            for node in tree:
                path = node.get("path", "")
                # Check direct match or directory prefix for author-managed folders
                is_authored = path in authored_files or any(
                    path.startswith(af.rsplit("/", 1)[0] + "/") for af in authored_files if "/" in af and not af.startswith("src/")
                )
                if is_authored:
                    size = node.get("size", 0)
                    ext = "." + path.split(".")[-1].lower() if "." in path else ""
                    if ext in EXT_MAP:
                        lang_name, lang_color = EXT_MAP[ext]
                        if lang_name not in languages:
                            languages[lang_name] = {"size": 0, "color": lang_color}
                        languages[lang_name]["size"] += size
                        total_bytes += size

    if total_bytes == 0:
        print("No language data found.")
        return

    sorted_langs = sorted(languages.items(), key=lambda x: x[1]["size"], reverse=True)
    top_langs = []
    for name, info in sorted_langs:
        pct = (info["size"] / total_bytes) * 100
        top_langs.append({
            "name": name,
            "color": info["color"],
            "pct": pct
        })

    # Dynamic row and geometry calculation
    num_rows = math.ceil(len(top_langs) / 2)
    row_h = 16
    grid_start_y = 206
    
    extra_rows = max(0, num_rows - 3)
    extra_h = extra_rows * row_h
    
    client_y = 51
    client_h = 219 + extra_h
    h_scroll_y = client_y + client_h
    total_h = h_scroll_y + 17 + 5
    outer_rect_h = total_h - 2
    path_bottom_y = total_h - 1
    
    prompt_y = 261 + extra_h

    # Progress bar parameters
    bar_x = 16
    bar_y = 181
    bar_width = 540
    bar_height = 8

    bar_rects = []
    curr_x = bar_x
    for i, item in enumerate(top_langs):
        seg_w = (item["pct"] / 100.0) * bar_width
        if seg_w < 3.5:
            seg_w = 3.5
        if curr_x + seg_w > bar_x + bar_width or i == len(top_langs) - 1:
            seg_w = (bar_x + bar_width) - curr_x
        if seg_w > 0:
            bar_rects.append(f'<rect x="{curr_x:.1f}" y="{bar_y}" width="{seg_w:.1f}" height="{bar_height}" fill="{item["color"]}" />')
            curr_x += seg_w

    # Dynamic 2-column language grid
    item_elements = []
    for i, item in enumerate(top_langs):
        col = i % 2
        row = i // 2
        ix = 20 if col == 0 else 260
        iy = grid_start_y + row * row_h
        pct_x = 126 if col == 0 else 366
        dot = f'<circle cx="{ix + 4}" cy="{iy - 4}" r="3.5" fill="{item["color"]}" />'
        txt = f'<text x="{ix + 12}" y="{iy}"><tspan fill="#FFFFFF">{item["name"]}</tspan><tspan x="{pct_x}" fill="#AAAAAA">{item["pct"]:>6.2f}%</tspan></text>'
        item_elements.append(f'{dot}{txt}')

    bar_svg = "".join(bar_rects)
    grid_svg = "\n    ".join(item_elements)

    # Scrollbar metrics
    v_scroll_h = client_h
    v_btn_y = v_scroll_h - 17
    v_arrow_p1 = v_btn_y + 6
    v_arrow_p2 = v_btn_y + 11

    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="740" height="{total_h}" viewBox="0 0 740 {total_h}">
  <defs>
    <linearGradient id="xpTitleGrad" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#1A4A15"/>
      <stop offset="25%" stop-color="#245A1E"/>
      <stop offset="65%" stop-color="#34742A"/>
      <stop offset="100%" stop-color="#468C36"/>
    </linearGradient>
    
    <linearGradient id="xpSheen" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#ffffff" stop-opacity="0.30"/>
      <stop offset="45%" stop-color="#ffffff" stop-opacity="0.06"/>
      <stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>

    <filter id="consoleInset" x="-2%" y="-2%" width="104%" height="104%">
      <feOffset dx="0" dy="2"/>
      <feGaussianBlur stdDeviation="2" result="blur"/>
      <feComposite operator="out" in="SourceGraphic" in2="blur"/>
      <feColorMatrix type="matrix" values="0 0 0 0 0   0 0 0 0 0   0 0 0 0 0   0 0 0 0.6 0"/>
      <feBlend mode="multiply" in2="SourceGraphic"/>
    </filter>
  </defs>

  <rect x="1" y="1" width="738" height="{outer_rect_h}" fill="#ece9d8" rx="4" ry="4"/>
  <path d="M1,5 A4,4 0 0,1 5,1 L735,1 A4,4 0 0,1 739,5 L739,{path_bottom_y} L1,{path_bottom_y} Z" 
        fill="none" stroke="#ffffff" stroke-width="1.5"/>
  <path d="M2,{path_bottom_y} L738,{path_bottom_y} L738,5 A4,4 0 0,0 735,2 L5,2 A4,4 0 0,0 2,5 Z" 
        fill="none" stroke="#888888" stroke-width="1.2"/>

  <!-- TITLE BAR -->
  <rect x="3" y="3" width="734" height="27" fill="url(#xpTitleGrad)" rx="3" ry="3"/>
  <rect x="3" y="3" width="734" height="13" fill="url(#xpSheen)" rx="3" ry="3"/>
  <line x1="5" y1="3.5" x2="735" y2="3.5" stroke="#8AE070" stroke-width="1" stroke-opacity="0.8"/>
  <line x1="3" y1="30" x2="737" y2="30" stroke="#163810" stroke-width="1"/>

  <!-- CRT Icon -->
  <g transform="translate(8, 8.5)" shape-rendering="crispEdges">
    <rect x="0" y="0" width="18" height="16" fill="#D4D0C8"/>
    <line x1="0" y1="0" x2="18" y2="0" stroke="#FFFFFF"/>
    <line x1="0" y1="0" x2="0" y2="16" stroke="#FFFFFF"/>
    <line x1="18" y1="0" x2="18" y2="16" stroke="#404040"/>
    <line x1="0" y1="16" x2="18" y2="16" stroke="#404040"/>
    <rect x="2" y="2" width="14" height="12" fill="#000000"/>
    <rect x="4" y="4.5" width="1.5" height="1.5" fill="#00FF66"/>
    <rect x="5.5" y="6" width="1.5" height="1.5" fill="#00FF66"/>
    <rect x="7" y="7.5" width="1.5" height="1.5" fill="#00FF66"/>
    <rect x="5.5" y="9" width="1.5" height="1.5" fill="#00FF66"/>
    <rect x="4" y="10.5" width="1.5" height="1.5" fill="#00FF66"/>
    <rect x="9.5" y="10.5" width="4.5" height="1.5" fill="#00FF66"/>
  </g>
  
  <text x="32" y="20.5" font-family="'Segoe UI', Tahoma, sans-serif" 
        font-size="12" font-weight="600" fill="#ffffff" letter-spacing="0.2"
        style="text-shadow: 1px 1px 1px #11280E;">
    C:\\WINDOWS\\system32\\cmd.exe - [Toolbox &amp; Environment]
  </text>

  <!-- 3D Controls -->
  <g transform="translate(674, 8.5)" shape-rendering="crispEdges">
    <g transform="translate(0, 0)">
      <rect x="0" y="0" width="18" height="16" fill="#D4D0C8"/>
      <line x1="0" y1="0" x2="18" y2="0" stroke="#FFFFFF"/>
      <line x1="0" y1="0" x2="0" y2="16" stroke="#FFFFFF"/>
      <line x1="1" y1="15" x2="17" y2="15" stroke="#808080"/>
      <line x1="17" y1="1" x2="17" y2="15" stroke="#808080"/>
      <line x1="0" y1="16" x2="18" y2="16" stroke="#000000"/>
      <line x1="18" y1="0" x2="18" y2="16" stroke="#000000"/>
      <rect x="5" y="11" width="7" height="2" fill="#000000"/>
    </g>
    <g transform="translate(20, 0)">
      <rect x="0" y="0" width="18" height="16" fill="#D4D0C8"/>
      <line x1="0" y1="0" x2="18" y2="0" stroke="#FFFFFF"/>
      <line x1="0" y1="0" x2="0" y2="16" stroke="#FFFFFF"/>
      <line x1="1" y1="15" x2="17" y2="15" stroke="#808080"/>
      <line x1="17" y1="1" x2="17" y2="15" stroke="#808080"/>
      <line x1="0" y1="16" x2="18" y2="16" stroke="#000000"/>
      <line x1="18" y1="0" x2="18" y2="16" stroke="#000000"/>
      <rect x="4" y="3.5" width="9" height="9" fill="none" stroke="#000000" stroke-width="1"/>
      <rect x="4" y="3.5" width="9" height="2.5" fill="#000000"/>
    </g>
    <g transform="translate(40, 0)">
      <rect x="0" y="0" width="18" height="16" fill="#D4D0C8"/>
      <line x1="0" y1="0" x2="18" y2="0" stroke="#FFFFFF"/>
      <line x1="0" y1="0" x2="0" y2="16" stroke="#FFFFFF"/>
      <line x1="1" y1="15" x2="17" y2="15" stroke="#808080"/>
      <line x1="17" y1="1" x2="17" y2="15" stroke="#808080"/>
      <line x1="0" y1="16" x2="18" y2="16" stroke="#000000"/>
      <line x1="18" y1="0" x2="18" y2="16" stroke="#000000"/>
      <rect x="5" y="4.5" width="2" height="1" fill="#000000"/>
      <rect x="11" y="4.5" width="2" height="1" fill="#000000"/>
      <rect x="6" y="5.5" width="2" height="1" fill="#000000"/>
      <rect x="10" y="5.5" width="2" height="1" fill="#000000"/>
      <rect x="7" y="6.5" width="4" height="2" fill="#000000"/>
      <rect x="6" y="8.5" width="2" height="1" fill="#000000"/>
      <rect x="10" y="8.5" width="2" height="1" fill="#000000"/>
      <rect x="5" y="9.5" width="2" height="1" fill="#000000"/>
      <rect x="11" y="9.5" width="2" height="1" fill="#000000"/>
    </g>
  </g>

  <!-- Menu Bar -->
  <rect x="3" y="30" width="734" height="20" fill="#ece9d8"/>
  <line x1="3" y1="50" x2="737" y2="50" stroke="#b0aba0" stroke-width="1"/>
  <g font-family="'Segoe UI', Tahoma, sans-serif" font-size="11" fill="#000000">
    <text x="8" y="44">File</text>
    <text x="34" y="44">Edit</text>
    <text x="60" y="44">View</text>
  </g>

  <!-- Client Area -->
  <rect x="4" y="51" width="714" height="{client_h}" fill="#000000" filter="url(#consoleInset)"/>
  <line x1="4" y1="51" x2="718" y2="51" stroke="#333" stroke-width="1"/>
  <line x1="4" y1="51" x2="4" y2="{h_scroll_y}" stroke="#333" stroke-width="1"/>
  <line x1="718" y1="51" x2="718" y2="{h_scroll_y}" stroke="#111" stroke-width="1"/>
  <line x1="4" y1="{h_scroll_y}" x2="718" y2="{h_scroll_y}" stroke="#111" stroke-width="1"/>

  <!-- Console Text -->
  <g font-family="'Lucida Console', 'Consolas', 'Courier New', monospace" font-size="12" fill="#FFFFFF">
    <text x="16" y="69">
      <tspan fill="#CCCCCC">C:\\Documents and Settings\\Kan&gt;</tspan>type stack.cfg
    </text>
    
    <text y="87"><tspan x="16" fill="#4AE371">[Languages]</tspan><tspan x="108" fill="#DDDDDD">Python · C / C++ · x86 Assembly · Java · JS / TS</tspan></text>
    <text y="103"><tspan x="16" fill="#4AE371">[Frameworks]</tspan><tspan x="108" fill="#DDDDDD">FastAPI · Flask · React · Tailwind CSS</tspan></text>
    <text y="119"><tspan x="16" fill="#4AE371">[Runtime/DB]</tspan><tspan x="108" fill="#DDDDDD">Docker · Git · SQLite · MySQL · JavaFX</tspan></text>
    <text y="135"><tspan x="16" fill="#4AE371">[Focus Area]</tspan><tspan x="108" fill="#DDDDDD">LLM Systems · GPU Architecture · CUDA</tspan></text>

    <text x="16" y="157">
      <tspan fill="#CCCCCC">C:\\Documents and Settings\\Kan&gt;</tspan>sysinfo -lang
    </text>
    <text x="16" y="173" fill="#E0E0E0" font-weight="bold">Languages Used (By File Size):</text>

    <!-- Progress Bar -->
    <g clip-path="url(#lang-bar-clip)">
      <clipPath id="lang-bar-clip">
        <rect x="{bar_x}" y="{bar_y}" width="{bar_width}" height="{bar_height}" rx="3" ry="3" />
      </clipPath>
      <rect x="{bar_x}" y="{bar_y}" width="{bar_width}" height="{bar_height}" fill="#222222" />
      {bar_svg}
    </g>

    <!-- Grid -->
    {grid_svg}

    <!-- Prompt -->
    <text x="16" y="{prompt_y}">
      <tspan fill="#CCCCCC">C:\\Documents and Settings\\Kan&gt;</tspan><tspan fill="#FFFFFF" font-weight="bold">▌<animate attributeName="opacity" values="1;0;1" dur="1s" repeatCount="indefinite"/></tspan>
    </text>
  </g>

  <!-- Vertical Scrollbar -->
  <g transform="translate(718, 51)">
    <rect width="17" height="{v_scroll_h}" fill="#ece9d8" stroke="#aaa" stroke-width="1"/>
    <rect width="17" height="17" fill="#ece9d8" stroke="#888"/>
    <polygon points="5,11 12,11 8.5,6" fill="#333"/>
    <rect y="{v_btn_y}" width="17" height="17" fill="#ece9d8" stroke="#888"/>
    <polygon points="5,{v_arrow_p1} 12,{v_arrow_p1} 8.5,{v_arrow_p2}" fill="#333"/>
    <rect x="2" y="22" width="13" height="42" fill="#d4d0c8" stroke="#999" rx="1"/>
    <rect x="3" y="23" width="11" height="20" fill="#ffffff" opacity="0.4" rx="1"/>
  </g>

  <!-- Horizontal Scrollbar -->
  <g transform="translate(4, {h_scroll_y})">
    <rect width="714" height="17" fill="#ece9d8" stroke="#aaa" stroke-width="1"/>
    <rect width="17" height="17" fill="#ece9d8" stroke="#888"/>
    <polygon points="11,5 6,8.5 11,12" fill="#333"/>
    <rect x="697" width="17" height="17" fill="#ece9d8" stroke="#888"/>
    <polygon points="703,5 708,8.5 703,12" fill="#333"/>
    <rect x="180" y="2" width="90" height="13" fill="#d4d0c8" stroke="#999" rx="1"/>
    <rect x="181" y="3" width="88" height="6" fill="#ffffff" opacity="0.4" rx="1"/>
  </g>
  <rect x="718" y="{h_scroll_y}" width="17" height="17" fill="#ece9d8" stroke="#aaa" stroke-width="1"/>
</svg>"""

    output_path = os.path.join("assets", "terminal_toolbox.svg")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(svg)
    print(f"Successfully generated dynamic {output_path} with {len(top_langs)} languages across {num_rows} rows!")

if __name__ == "__main__":
    main()
