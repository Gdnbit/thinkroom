# ThinkRoom third-party notices

Original contributions use Apache-2.0. External libraries, fonts, test utilities
and container images retain upstream licenses. This source snapshot does not
vendor installed dependencies or their binaries.

PDF processing uses pypdfium2 (Apache-2.0 OR BSD-3-Clause) and PDFium (BSD-style
with additional component notices). PyMuPDF is not a runtime or test requirement.
Synthetic PDFs are generated with ReportLab (BSD).

Primary sources:
- https://github.com/pypdfium2-team/pypdfium2
- https://pdfium.googlesource.com/pdfium/+/refs/heads/main/LICENSE
- https://www.reportlab.com/opensource/

Exact dependency inventory: `licenses/dependencies.json`. Binary/container
redistributors must preserve installed upstream licenses and notices, including
pypdfium2's bundled PDFium notices. External social-data tools and images have
independent terms and are not relicensed by this project.

`examples/` is original synthetic material. No right to redistribute private
research documents is assumed or granted by the project license.

The inventory also contains weak-copyleft dependencies (including frozendict,
psycopg2, certifi and pathspec) and the CC-BY-4.0 caniuse-lite dataset. Their terms
remain applicable; Apache-2.0 does not replace them. This release distributes
project sources and lock files, not installed packages or container images.
Future binary/container releases need their own bundled license/attribution and
source/relinking compliance review where required.

Lucide icons retain their ISC license. The CSS names system font families but
does not bundle font files. The ThinkRoom SVG mark is project-owned. Synthetic
fixture PDFs use ReportLab standard/CID font references rather than shipping
private fonts or research pages. Development/production container base images
and optional data-provider integrations are separately licensed.
