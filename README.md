# KiCAD MCP Server

A production-ready Model Context Protocol (MCP) server that enables AI assistants like Claude to interact with KiCAD for PCB design automation. Built on the MCP 2025-06-18 specification, this server provides comprehensive tool schemas and real-time project state access for intelligent PCB design workflows.

## Overview

The [Model Context Protocol](https://modelcontextprotocol.io/) is an open standard from Anthropic that allows AI assistants to securely connect to external tools and data sources. This implementation provides a standardized bridge between AI assistants and KiCAD, enabling natural language control of professional PCB design operations.

**Key Capabilities:**
- 52 fully-documented tools with JSON Schema validation
- 8 dynamic resources exposing project state
- Full MCP 2025-06-18 protocol compliance
- Cross-platform support (Linux, Windows, macOS)
- Real-time KiCAD UI integration
- Comprehensive error handling and logging

## What's New in v2.1.0

### Comprehensive Tool Schemas
Every tool now includes complete JSON Schema definitions with:
- Detailed parameter descriptions and constraints
- Input validation with type checking
- Required vs. optional parameter specifications
- Enumerated values for categorical inputs
- Clear documentation of what each tool does

### Resources Capability
Access project state without executing tools:
- `kicad://project/current/info` - Project metadata
- `kicad://project/current/board` - Board properties
- `kicad://project/current/components` - Component list (JSON)
- `kicad://project/current/nets` - Electrical nets
- `kicad://project/current/layers` - Layer stack configuration
- `kicad://project/current/design-rules` - Current DRC settings
- `kicad://project/current/drc-report` - Design rule violations
- `kicad://board/preview.png` - Board visualization (PNG)

### Protocol Compliance
- Updated to MCP SDK 1.21.0 (latest)
- Full JSON-RPC 2.0 support
- Proper capability negotiation
- Standards-compliant error codes

## F0 CERN evidence ingestion and catalog search

`python -m adapters.cern` is an independent, standard-library-only ingestion gate.
The MCP server additionally exposes `search_cern_components`, which performs a
bounded, read-only text search over a CERN SQLite snapshot. Neither capability imports
KiCad, selects parts automatically, downloads evidence, or modifies `CERN.sqlite`.
CERN's corpus is institutional evidence, **not manufacturer truth**.
All normalized components and representations remain `unverified`, including
successfully resolved library geometry. Manufacturer datasheet crosschecking is
explicitly `not_implemented`.

`search_cern_components` returns matching source records with the source hash,
normalized identity, lifecycle observation, and explicit evidence limitations. It
does not rank suitability, verify availability, or recommend a component. By default
it searches the repository's bundled `CERN.sqlite`; callers may provide a different
quiescent SQLite snapshot and request up to 100 results.

Run from this repository's root with Python 3.9 or newer:

```bash
# Offline extraction: complete source capture, explicitly incomplete geometry.
python -m adapters.cern --sqlite CERN.sqlite --output generated/cern

# Real symbol/pad joins against an existing CERN library checkout or extracted archive.
python -m adapters.cern --sqlite CERN.sqlite --output generated/cern \
  --libraries /absolute/path/to/cern-kicad-libs

# Optional strict geometry gate; writes the report and exits 2 when incomplete.
python -m adapters.cern --sqlite CERN.sqlite --output generated/cern \
  --libraries /absolute/path/to/cern-kicad-libs --require-complete-geometry
```

The optional library input is the root of
[`ohwr/cern-kicad-libs`](https://gitlab.com/ohwr/cern-kicad-libs), containing
`sym-lib-table`, `fp-lib-table`, `SchLib/`, and `PcbLib/`. Supply an existing checkout
or download/extract an upstream archive yourself; the adapter never clones,
downloads, executes library content, or expands arbitrary environment variables.
It resolves library nicknames through the tables, accepting only bounded
`${CERN_LIB_DIR}/...` KiCad paths. Symbol names containing `/` are valid lookup
keys, not filesystem paths; footprint traversal and escaping symlinks are refused.
For reproducibility, use an immutable upstream revision. Reports record both table
hashes and each joined geometry file's SHA-256, rather than guessing a Git revision.

### Artifacts and identity model

Outputs under `generated/cern/` are intentionally gitignored: the complete corpus
and external libraries are large and regenerable. The six runtime outputs are:

| File | Contract and purpose |
| --- | --- |
| `components.raw.jsonl` | Every source row, every original column/value, table, row key, ordinal, original part number, source database hash |
| `components.normalized.jsonl` | Manufacturer + manufacturer part number identities, CERN part numbers, source links, per-field evidence, **all** lifecycle observations, representation links |
| `representations.jsonl` | Two source-linked slots per row (symbol and footprint), original reference, parsed nickname/name, resolution status, geometry/provenance where available |
| `anomalies.jsonl` | Every conservative rule finding, supporting evidence, source/component links and review action |
| `quality_report.json` | Extraction integrity checks, lifecycle/reference/geometry counters, all rule totals, deterministic top 100 anomalies and explicit limitations |
| `sqlite_inventory.json` | Every user table's original DDL, column metadata and expected row count, including empty tables |

Reusable entrypoints are `schemas/component_identity.schema.json`,
`schemas/component_evidence.schema.json`, `schemas/component_representation.schema.json`,
and `schemas/lifecycle_state.schema.json`. Field evidence records each source
column's `source_value`, `normalized_value`, source ID, `claim`, `confidence`,
and `verification_state`. Claims remain unverified institutional source assertions:
identity and lifecycle fields are normalized; other values are preserved verbatim.
Merged identities retain every source observation, including conflicting claims.

Draft 2020-12 contracts are also in `schemas/cern.*.schema.json`, with shared definitions
in `schemas/cern.schema.json`. JSONL contracts apply to **each line**, not the file
as a single JSON document. BLOBs use `{"$sqlite_blob_base64": "..."}` and nonfinite
SQLite REALs use `{"$sqlite_float": "inf"}` (or `"-inf"`), avoiding lossy JSON coercion.

Identity normalization collapses whitespace, case-folds the manufacturer, and
**preserves MPN case**. No manufacturer aliasing, fuzzy matching, or splitting of
compound MPNs occurs. Missing identities remain separate per source row. Multiple
symbols, footprints, package variants, or lifecycle observations never overwrite
one another and are not, by themselves, identity collisions. Original values remain
available in the raw records; normalized lifecycle labels do not imply current
availability or manufacturer endorsement. Unknown/typo labels remain `unknown`.

IDs and ordering are deterministic for identical input bytes. Source IDs incorporate
the database hash, table, and SQLite row identity; normalized complete component IDs
depend only on manufacturer/MPN. A changed source database intentionally changes
source and representation IDs. Without-rowid tables retain primary keys; if every
rowid alias is shadowed, deterministic value ordering plus ordinal preserves even
duplicate rows. SQLite connections use URI `mode=ro`; hashes are checked before and
after extraction. A nonempty WAL is rejected: supply a quiescent standalone snapshot
rather than allowing the adapter to checkpoint or modify the source.

### Conservative anomaly rules and gate semantics

- `pin_count_mismatch`: compares positive declared pin counts with unique physical
  symbol numbers and footprint pad numbers. Multiunit/common pins, repeated pads,
  alternate symbol bodies, and inheritance are handled without double-counting.
  Differing alternate number sets, unsupported inherited/local pin combinations,
  unnumbered electrical pads, and pinless geometry remain non-comparable.
  Unnumbered non-plated mechanical holes do not increase electrical pad counts.
- `package_mismatch`: flags only conflicting explicit package families, not guessed
  package dimensions or naïve numerical suffix comparisons. For example, QFN56
  with a 57th exposed thermal pad is not inherently a package mismatch.
- `mounting_mismatch`: compares explicit source SMD yes/no with homogeneous
  electrical pad types. Mixed/unknown mounting is not guessed.
- `identity_collision`: flags known incompatible source families sharing an
  identity; ordinary SMD/through-hole or symbol variants are not collisions.
- `datasheet_placeholder_unresolved`: records unexpanded `${...}` references.
  Neither a path nor its absence establishes datasheet correctness.
- `lifecycle_normalization_needed`: flags unrecognized source lifecycle vocabulary;
  recognized variants normalize without pretending that typos are authoritative.
- `semantic_generation_mismatch`: a curated **RP2040 golden negative** catches
  Cortex-M33, Hazard3, 150 MHz, 520 KB SRAM and internal-flash claims on the bare
  RP2040 identity. The source description is preserved, never silently repaired.
  This is not a general semantic validator or an implemented datasheet crosscheck.

Default success means extraction integrity passed, **not** that the corpus is safe
for design. Missing/malformed references and absent library entries are explicitly
counted. `geometry_complete` is false for unresolved or ambiguous geometry, including
conservatively non-comparable mechanical parts. Use `--require-complete-geometry`
when incompleteness must fail automation. Exit 1 indicates input/integrity/I/O errors;
exit 2 indicates an unmet strict geometry gate. Findings are review candidates,
ranked by severity, rule name, and stable anomaly ID; even high-severity findings
retain unverified evidence status.

### Measured first-cut corpus and tests

The supplied SQLite SHA-256
`ccd83a31cbda0d55a5a3029fb22e42e80da651eefa11b9172f96d098b2e345a5`
contains **55 tables and 24,223 rows**, captured losslessly as **20,278 normalized
identities** and **48,446 representation slots**. A full join against the upstream
master archive downloaded on 2026-09-19 resolved 24,222 symbols and 24,196 footprints:
**28 unresolved slots** and **1,584 ambiguous/non-comparable geometries** remain.
These are observations for that evidence snapshot, not permanent expected totals.

The measured run found 3,067 pin-count, 38 explicit package, 239 mounting, zero
incompatible-family identity collisions, 24,049 datasheet-placeholder, two lifecycle
vocabulary, and one RP2040 semantic finding. The report retains all **27,396**
findings; the RP2040 golden negative appears in the top 100. Counts are intentionally
not corrected away, and new library revisions may change them.

The fixture suite covers lossless extraction, read-only enforcement, deterministic
outputs, identity/representation separation, lifecycle preservation, path boundaries,
multiunit/alternate/inherited symbols, unique pads, conservative anomalies, schema
contracts (including rejecting invalid structures), and the real SQLite golden
negative. It uses the existing pytest dependency and no new runtime dependencies:

```bash
python -m pytest -o addopts= tests/test_cern_ingestion.py tests/test_platform_helper.py -q
```

The test contract checker implements only the JSON Schema assertion keywords used
by these contracts, failing if unsupported assertion keywords are introduced; it is
not presented as a general-purpose JSON Schema validator.

## Available Tools

The server provides 52 tools organized into functional categories:

### Project Management (4 tools)
- `create_project` - Initialize new KiCAD projects
- `open_project` - Load existing project files
- `save_project` - Save current project state
- `get_project_info` - Retrieve project metadata

### Board Operations (9 tools)
- `set_board_size` - Configure PCB dimensions
- `add_board_outline` - Create board edge (rectangle, circle, polygon)
- `add_layer` - Add custom layers to stack
- `set_active_layer` - Switch working layer
- `get_layer_list` - List all board layers
- `get_board_info` - Retrieve board properties
- `get_board_2d_view` - Generate board preview image
- `add_mounting_hole` - Place mounting holes
- `add_board_text` - Add text annotations

### Component Placement (10 tools)
- `place_component` - Place single component with footprint
- `move_component` - Reposition existing component
- `rotate_component` - Rotate component by angle
- `delete_component` - Remove component from board
- `edit_component` - Modify component properties
- `get_component_properties` - Query component details
- `get_component_list` - List all placed components
- `place_component_array` - Create component grids/patterns
- `align_components` - Align multiple components
- `duplicate_component` - Copy existing component

### Routing & Nets (8 tools)
- `add_net` - Create electrical net
- `route_trace` - Route copper traces
- `add_via` - Place vias for layer transitions
- `delete_trace` - Remove traces
- `get_nets_list` - List all nets
- `create_netclass` - Define net class with rules
- `add_copper_pour` - Create copper zones/pours
- `route_differential_pair` - Route differential signals

### Library Management (4 tools)
- `list_libraries` - List available footprint libraries
- `search_footprints` - Search for footprints
- `list_library_footprints` - List footprints in library
- `get_footprint_info` - Get footprint details

### Design Rules (4 tools)
- `set_design_rules` - Configure DRC parameters
- `get_design_rules` - Retrieve current rules
- `run_drc` - Execute design rule check
- `get_drc_violations` - Get DRC error report

### Export (5 tools)
- `export_gerber` - Generate Gerber fabrication files
- `export_pdf` - Export PDF documentation
- `export_svg` - Create SVG vector graphics
- `export_3d` - Generate 3D models (STEP/VRML)
- `export_bom` - Produce bill of materials

### Schematic Design (6 tools)
- `create_schematic` - Initialize new schematic
- `load_schematic` - Open existing schematic
- `add_schematic_component` - Place symbols
- `add_schematic_wire` - Connect component pins
- `list_schematic_libraries` - List symbol libraries
- `export_schematic_pdf` - Export schematic PDF

### UI Management (2 tools)
- `check_kicad_ui` - Check if KiCAD is running
- `launch_kicad_ui` - Launch KiCAD application

## Prerequisites

### Required Software

**KiCAD 9.0 or Higher**
- Download from [kicad.org/download](https://www.kicad.org/download/)
- Must include Python module (pcbnew)
- Verify installation:
  ```bash
  python3 -c "import pcbnew; print(pcbnew.GetBuildVersion())"
  ```

**Node.js 18 or Higher**
- Download from [nodejs.org](https://nodejs.org/)
- Verify: `node --version` and `npm --version`

**Python 3.10 or Higher**
- Usually included with KiCAD
- Required packages (auto-installed):
  - kicad-skip >= 0.1.0 (schematic support)
  - Pillow >= 9.0.0 (image processing)
  - cairosvg >= 2.7.0 (SVG rendering)
  - colorlog >= 6.7.0 (logging)
  - pydantic >= 2.5.0 (validation)
  - requests >= 2.32.5 (HTTP client)
  - python-dotenv >= 1.0.0 (environment)

**MCP Client**
Choose one:
- [Claude Desktop](https://claude.ai/download) - Official Anthropic desktop app
- [Claude Code](https://docs.claude.com/claude-code) - Official CLI tool
- [Cline](https://github.com/cline/cline) - VSCode extension

### Supported Platforms
- **Linux** (Ubuntu 22.04+, Fedora, Arch) - Primary platform, fully tested
- **Windows 10/11** - Fully supported with automated setup
- **macOS** - Experimental support

## Installation

### Linux (Ubuntu/Debian)

```bash
# Install KiCAD 9.0
sudo add-apt-repository --yes ppa:kicad/kicad-9.0-releases
sudo apt-get update
sudo apt-get install -y kicad kicad-libraries

# Install Node.js
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt-get install -y nodejs

# Clone and build
git clone https://github.com/mixelpixx/KiCAD-MCP-Server.git
cd KiCAD-MCP-Server
npm install
pip3 install -r requirements.txt
npm run build

# Verify
python3 -c "import pcbnew; print(pcbnew.GetBuildVersion())"
```

### Windows 10/11

**Automated Setup (Recommended):**
```powershell
git clone https://github.com/mixelpixx/KiCAD-MCP-Server.git
cd KiCAD-MCP-Server
.\setup-windows.ps1
```

The script will:
- Detect KiCAD installation
- Verify prerequisites
- Install dependencies
- Build project
- Generate configuration
- Run diagnostics

**Manual Setup:**
See [Windows Installation Guide](docs/WINDOWS_SETUP.md) for detailed instructions.

### macOS

```bash
# Install KiCAD 9.0 from kicad.org/download/macos

# Install Node.js
brew install node@20

# Clone and build
git clone https://github.com/mixelpixx/KiCAD-MCP-Server.git
cd KiCAD-MCP-Server
npm install
pip3 install -r requirements.txt
npm run build
```

## Configuration

### Claude Desktop

Edit configuration file:
- **Linux/macOS:** `~/.config/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

**Configuration:**
```json
{
  "mcpServers": {
    "kicad": {
      "command": "node",
      "args": ["/path/to/KiCAD-MCP-Server/dist/index.js"],
      "env": {
        "PYTHONPATH": "/path/to/kicad/python",
        "LOG_LEVEL": "info"
      }
    }
  }
}
```

**Platform-specific PYTHONPATH:**
- **Linux:** `/usr/lib/kicad/lib/python3/dist-packages`
- **Windows:** `C:\Program Files\KiCad\9.0\lib\python3\dist-packages`
- **macOS:** `/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.11/lib/python3.11/site-packages`

### Cline (VSCode)

Edit: `~/.config/Code/User/globalStorage/saoudrizwan.claude-dev/settings/cline_mcp_settings.json`

Use the same configuration format as Claude Desktop above.

### Claude Code

Claude Code automatically detects MCP servers in the current directory. No additional configuration needed.

## Usage Examples

### Basic PCB Design Workflow

```text
Create a new KiCAD project named 'LEDBoard' in my Documents folder.
Set the board size to 50mm x 50mm and add a rectangular outline.
Place a mounting hole at each corner, 3mm from the edges, with 3mm diameter.
Add text 'LED Controller v1.0' on the front silkscreen at position x=25mm, y=45mm.
```

### Component Placement

```text
Place an LED at x=10mm, y=10mm using footprint LED_SMD:LED_0805_2012Metric.
Create a grid of 4 resistors (R1-R4) starting at x=20mm, y=20mm with 5mm spacing.
Align all resistors horizontally and distribute them evenly.
```

### Routing

```text
Create a net named 'LED1' and route a 0.3mm trace from R1 pad 2 to LED1 anode.
Add a copper pour for GND on the bottom layer covering the entire board.
Create a differential pair for USB_P and USB_N with 0.2mm width and 0.15mm gap.
```

### Design Verification

```text
Set design rules with 0.15mm clearance and 0.2mm minimum track width.
Run a design rule check and show me any violations.
Export Gerber files to the 'fabrication' folder.
```

### Using Resources

Resources provide read-only access to project state:

```text
Show me the current component list.
What are the current design rules?
Display the board preview.
List all electrical nets.
```

## Architecture

### MCP Protocol Layer
- **JSON-RPC 2.0 Transport:** Bi-directional communication via STDIO
- **Protocol Version:** MCP 2025-06-18
- **Capabilities:** Tools (52), Resources (8)
- **Error Handling:** Standard JSON-RPC error codes

### TypeScript Server (`src/`)
- Implements MCP protocol specification
- Manages Python subprocess lifecycle
- Handles message routing and validation
- Provides logging and error recovery

### Python Interface (`python/`)
- **kicad_interface.py:** Main entry point, MCP message handler
- **schemas/tool_schemas.py:** JSON Schema definitions for all tools
- **resources/resource_definitions.py:** Resource handlers and URIs
- **commands/:** Modular command implementations
  - `project.py` - Project operations
  - `board.py` - Board manipulation
  - `component.py` - Component placement
  - `routing.py` - Trace routing and nets
  - `design_rules.py` - DRC operations
  - `export.py` - File generation
  - `schematic.py` - Schematic design
  - `library.py` - Footprint libraries

### KiCAD Integration
- **pcbnew API:** Direct Python bindings to KiCAD
- **kicad-skip:** Schematic file manipulation
- **Platform Detection:** Cross-platform path handling
- **UI Management:** Automatic KiCAD UI launch/detection

## Development

### Building from Source

```bash
# Install dependencies
npm install
pip3 install -r requirements.txt

# Build TypeScript
npm run build

# Watch mode for development
npm run dev
```

### Running Tests

```bash
# TypeScript tests
npm run test:ts

# Python tests
npm run test:py

# All tests with coverage
npm run test:coverage
```

### Linting and Formatting

```bash
# Lint TypeScript and Python
npm run lint

# Format code
npm run format
```

## Troubleshooting

### Server Not Appearing in Client

**Symptoms:** MCP server doesn't show up in Claude Desktop or Cline

**Solutions:**
1. Verify build completed: `ls dist/index.js`
2. Check configuration paths are absolute
3. Restart MCP client completely
4. Check client logs for error messages

### Python Module Import Errors

**Symptoms:** `ModuleNotFoundError: No module named 'pcbnew'`

**Solutions:**
1. Verify KiCAD installation: `python3 -c "import pcbnew"`
2. Check PYTHONPATH in configuration matches your KiCAD installation
3. Ensure KiCAD was installed with Python support

### Tool Execution Failures

**Symptoms:** Tools fail with unclear errors

**Solutions:**
1. Check server logs: `~/.kicad-mcp/logs/kicad_interface.log`
2. Verify a project is loaded before running board operations
3. Ensure file paths are absolute, not relative
4. Check tool parameter types match schema requirements

### Windows-Specific Issues

**Symptoms:** Server fails to start on Windows

**Solutions:**
1. Run automated diagnostics: `.\setup-windows.ps1`
2. Verify Python path uses double backslashes: `C:\\Program Files\\KiCad\\9.0`
3. Check Windows Event Viewer for Node.js errors
4. See [Windows Troubleshooting Guide](docs/WINDOWS_TROUBLESHOOTING.md)

### Getting Help

1. Check the [GitHub Issues](https://github.com/mixelpixx/KiCAD-MCP-Server/issues)
2. Review server logs: `~/.kicad-mcp/logs/kicad_interface.log`
3. Open a new issue with:
   - Operating system and version
   - KiCAD version (`python3 -c "import pcbnew; print(pcbnew.GetBuildVersion())"`)
   - Node.js version (`node --version`)
   - Full error message and stack trace
   - Relevant log excerpts

## Project Status

**Current Version:** 2.1.0-alpha

**Production-Ready Features:**
- Project creation and management
- Board outline and sizing
- Layer management
- Component placement (with library integration needed)
- Mounting holes and text annotations
- Design rule checking
- Export to Gerber, PDF, SVG, 3D
- Schematic creation and editing
- UI auto-launch
- Full MCP protocol compliance

**In Development:**
- JLCPCB parts integration
- Digikey API integration
- Advanced routing algorithms
- Real-time UI synchronization via IPC API
- Smart BOM management

**Roadmap:**
- AI-assisted component selection
- Design pattern library (Arduino shields, RPi HATs)
- Interactive design review mode
- Automated documentation generation
- Multi-board projects

See [ROADMAP.md](docs/ROADMAP.md) for detailed development timeline.

## Contributing

Contributions are welcome! Please follow these guidelines:

1. **Report Bugs:** Open an issue with reproduction steps
2. **Suggest Features:** Describe use case and expected behavior
3. **Submit Pull Requests:**
   - Fork the repository
   - Create a feature branch
   - Follow existing code style
   - Add tests for new functionality
   - Update documentation
   - Submit PR with clear description

See [CONTRIBUTING.md](CONTRIBUTING.md) for detailed guidelines.

## License

This project is licensed under the MIT License. See [LICENSE](LICENSE) for details.

## Acknowledgments

- Built on the [Model Context Protocol](https://modelcontextprotocol.io/) by Anthropic
- Powered by [KiCAD](https://www.kicad.org/) open-source PCB design software
- Uses [kicad-skip](https://github.com/kicad-skip) for schematic manipulation

## Citation

If you use this project in your research or publication, please cite:

```bibtex
@software{kicad_mcp_server,
  title = {KiCAD MCP Server: AI-Assisted PCB Design},
  author = {mixelpixx},
  year = {2025},
  url = {https://github.com/mixelpixx/KiCAD-MCP-Server},
  version = {2.1.0-alpha}
}
```
