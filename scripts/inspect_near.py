import sys
import pymupdf

doc_name, page_no, anchor = sys.argv[1], int(sys.argv[2]), sys.argv[3]
with pymupdf.open(f"samples/financebench/pdfs/{doc_name}.pdf") as doc:
    words = doc[page_no].get_text("words")

for a in [w for w in words if w[4] == anchor and w[0] < 100]:
    ay = (a[1] + a[3]) / 2
    print(f"--- '{anchor}' at y={ay:.1f}, x={a[0]:.0f}")
    near = [w for w in words if abs((w[1] + w[3]) / 2 - ay) <= 12]
    for w in sorted(near, key=lambda w: ((w[1] + w[3]) / 2, w[0])):
        print(f"   y={(w[1] + w[3]) / 2:6.1f} x0={w[0]:5.0f} x1={w[2]:5.0f} {w[4]}")
