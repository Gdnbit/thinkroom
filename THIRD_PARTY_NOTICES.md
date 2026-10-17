# Third-party notices

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
