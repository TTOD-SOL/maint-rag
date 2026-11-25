from rag.chunking import chunk_document, est_tokens, is_heading

SENT = "The technician inspects the conveyor drive belt for wear and proper tension."


def pages(n: int = 6, sentences: int = 40) -> list[dict]:
    out = []
    for i in range(1, n + 1):
        body = "\n".join(f"{SENT} Step {i}.{j}." for j in range(sentences))
        out.append({"page": str(i), "text": f"Section {i} Belt Inspection\n{body}" if i % 2 else body})
    return out


def test_heading_detection():
    assert is_heading("Energy Control Program")
    assert is_heading("2.1 Air Management")
    assert is_heading("What is lockout/tagout?")
    assert not is_heading("the belt was replaced and the tension adjusted per spec.")
    assert not is_heading("• bullet item")


def test_chunks_respect_size_and_metadata():
    chunks = chunk_document(pages(), "doc.pdf", "Doc", target=200, max_tokens=300, min_tokens=80, overlap=0.15)
    assert len(chunks) > 3
    for c in chunks:
        assert est_tokens(c.text) <= 300 + 30  # heading prefix may add a few tokens
        assert c.source == "doc.pdf" and c.title == "Doc"
        assert c.page in {str(i) for i in range(1, 7)}
        md = c.metadata()
        assert {"source", "title", "page", "chunk_id"} <= md.keys()


def test_chunk_ids_are_stable_and_unique():
    a = chunk_document(pages(), "doc.pdf", "Doc", target=200, max_tokens=300, min_tokens=80)
    b = chunk_document(pages(), "doc.pdf", "Doc", target=200, max_tokens=300, min_tokens=80)
    assert [c.chunk_id for c in a] == [c.chunk_id for c in b]
    assert len({c.chunk_id for c in a}) == len(a)


def test_overlap_carries_text_forward():
    chunks = chunk_document(pages(1, 80), "doc.pdf", "Doc", target=150, max_tokens=250, min_tokens=50, overlap=0.2)
    assert len(chunks) >= 2
    tail = chunks[0].text.split()[-5:]
    assert " ".join(tail) in chunks[1].text


def test_no_overlap():
    chunks = chunk_document(pages(1, 80), "doc.pdf", "Doc", target=150, max_tokens=250, min_tokens=50, overlap=0.0)
    tail = " ".join(chunks[0].text.split()[-8:])
    assert tail not in chunks[1].text
