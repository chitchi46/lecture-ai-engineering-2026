"""第5回: ページ単位のベクトル検索。"""

import argparse
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from collections import defaultdict, deque
from pathlib import Path

from google import genai
from google.genai import types
from pydantic_ai.exceptions import ModelHTTPError
import sqlite_vec

from app.config import (
    EMBEDDING_DIMENSIONS, EMBEDDING_MAX_TOKENS, EMBEDDING_MODEL,
    RAG_DB, RAG_DOCUMENT_IDS, RAG_SOURCE, RAG_TOP_K, RAG_TREE, get_api_key,
    RAG_GRAPH, GRAPH_START_K, GRAPH_HOPS, GRAPH_MAX_RESULTS,
    HYBRID_SEED_K, HYBRID_EXPAND_K,
)


def get_all_materials() -> str:
    """全資料を、講義・章・ページの情報付きで1つの文字列にまとめる。"""
    documents = json.loads(RAG_SOURCE.read_text(encoding="utf-8"))
    return "\n\n".join(
        f"資料: {document['id']} / {document['title']}\n"
        f"章: {section['section']}\n"
        f"ページ: PDF p.{page['page']} / {page['title']}\n{page['content']}"
        for document in documents
        for section in document["content"]
        for page in section["content"]
    )

def load_pages(source: Path = RAG_SOURCE) -> list[dict]:
    """資料→章→ページのJSONを、対象資料のページ一覧に変換する。"""
    documents = json.loads(source.read_text(encoding="utf-8"))
    pages = []
    for document in documents:
        if document["id"] not in RAG_DOCUMENT_IDS:
            continue
        for section in document["content"]:
            for page in section["content"]:
                pages.append({"page_id": page["id"], "document_id": document["id"],
                              "lecture": int(document["id"][3:5]), "document_title": document["title"],
                              "section": section["section"], "title": page["title"],
                              "page": page["page"], "content": page["content"]})
    return pages


def browse_tree(node_id: str | None = None) -> dict:
    """直下のページは先頭文、指定したページ自身は全文を返す。"""
    tree = json.loads(RAG_TREE.read_text(encoding="utf-8"))
    tree["children"] = [node for node in tree["children"] if node["id"] in RAG_DOCUMENT_IDS]
    node_id = node_id or tree["id"]
    pages = {page["page_id"]: page for page in load_pages(RAG_SOURCE)}

    def describe(node, full=False):
        info = {key: node[key] for key in ("id", "kind", "title")}
        if node["kind"] == "page":
            info.update({key: value for key, value in pages[node["id"]].items() if key != "page_id"})
            if not full:
                info["content"] = re.split(r"(?<=[。！？])|\n", info["content"].strip(), maxsplit=1)[0]
        return info

    remaining = [tree]
    while remaining:
        node = remaining.pop()
        if node["id"] == node_id:
            return {
                "node": describe(node, full=True),
                "children": [describe(child) for child in node.get("children", [])],
            }
        remaining.extend(node.get("children", []))
    raise ValueError("対象の目次ノードが見つかりません。")


def read_page(page_id: str) -> dict:
    """第1〜3回の本講義から、指定した1ページの本文と引用情報を返す。"""
    for page in load_pages(RAG_SOURCE):
        if page["page_id"] == page_id:
            return page
    raise ValueError("対象のページが見つかりません。")


def normalized_vector(values: list[float]) -> list[float]:
    """ベクトルの長さを1に揃える。"""
    norm = math.hypot(*values)
    if norm == 0:
        raise ValueError("埋め込みがゼロベクトルです。")
    return [value / norm for value in values]


def embed_text(text: str, task_type: str) -> list[float]:
    """Geminiで文章を埋め込む。入力上限を超えた場合は切り捨てず停止する。"""
    with genai.Client(api_key=get_api_key()) as client:
        count = client.models.count_tokens(model=EMBEDDING_MODEL, contents=text).total_tokens
        if count is None or count > EMBEDDING_MAX_TOKENS:
            raise ValueError(f"埋め込み入力が上限を超えるか計測不能です: {count} tokens。")
        response = client.models.embed_content(
            model=EMBEDDING_MODEL, contents=text,
            config=types.EmbedContentConfig(task_type=task_type, output_dimensionality=EMBEDDING_DIMENSIONS),
        )
    embedding = response.embeddings[0]
    if embedding.statistics and embedding.statistics.truncated:
        raise ValueError("埋め込みAPIが本文を切り捨てました。")
    return normalized_vector(embedding.values)


@contextmanager
def open_index(path: Path):
    """既存の検索DBを読み取り専用で開き、終了時に閉じる。"""
    if not path.is_file():
        raise FileNotFoundError("検索DBがありません。")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        db.row_factory = sqlite3.Row
        db.enable_load_extension(True)
        sqlite_vec.load(db)
        db.enable_load_extension(False)
        yield db
    finally:
        db.close()


def search(query: str) -> list[dict]:
    """質問を埋め込み、近いページを最大RAG_TOP_K件返す。"""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("検索語が空です。")
    with open_index(RAG_DB) as db:
        vector = embed_text(query.strip(), "RETRIEVAL_QUERY")
        rows = db.execute("""
            SELECT page_id, document_id, lecture, document_title, section, title, page, content,
                   vec_distance_cosine(embedding, ?) AS distance
            FROM chunks ORDER BY distance, page_id LIMIT ?
        """, (sqlite_vec.serialize_float32(vector), RAG_TOP_K))
        return [dict(row) for row in rows]


def search_graph(query: str) -> dict:
    """ベクトル検索を入口に、前後ページと共通用語で候補を広げる。"""
    pages = {page["page_id"]: page for page in load_pages(RAG_SOURCE)}
    graph = json.loads(RAG_GRAPH.read_text(encoding="utf-8"))
    nodes = {node["id"]: node for node in graph["nodes"]
             if node["kind"] == "term" or node["id"] in pages}
    adjacent = defaultdict(set)
    for edge in graph["edges"]:
        a, b = edge["source"], edge["target"]
        if edge["relation"] in ("next_page", "mentions") and a in nodes and b in nodes:
            adjacent[a].add(b)
            adjacent[b].add(a)

    seeds = [hit for hit in search(query) if hit["page_id"] in pages][:min(GRAPH_START_K, GRAPH_MAX_RESULTS)]
    seed_ids = {seed["page_id"] for seed in seeds}
    results, candidates = [], {}
    for rank, seed in enumerate(seeds):
        root = seed["page_id"]
        results.append({**pages[root], "distance": seed["distance"], "hops": 0,
                        "path": [{"id": root, "title": pages[root]["title"]}]})
        queue = deque([(root, [root])])
        visited = {root}
        while queue:
            current, path = queue.popleft()
            if len(path) - 1 >= GRAPH_HOPS:
                continue
            # 同じ深さなら、接続先が少ない用語からたどる。
            neighbors = sorted(adjacent[current], key=lambda n: (len(adjacent[n]), n))
            for neighbor in neighbors:
                if neighbor in visited:
                    continue
                visited.add(neighbor)
                route = path + [neighbor]
                queue.append((neighbor, route))
                if neighbor not in pages or neighbor in seed_ids:
                    continue
                frequency = max((len(adjacent[n]) for n in route if nodes[n]["kind"] == "term"), default=0)
                priority = (len(route) - 1, frequency, rank, neighbor)
                candidate = {**pages[neighbor], "hops": len(route) - 1,
                             "path": [{"id": n, "title": nodes[n]["title"]} for n in route]}
                if neighbor not in candidates or priority < candidates[neighbor][0]:
                    candidates[neighbor] = (priority, candidate)
    expanded = [candidate for _, candidate in sorted(candidates.values())]
    return {"seeds": results, "expanded": expanded[:max(0, GRAPH_MAX_RESULTS - len(results))],
            "max_hops": GRAPH_HOPS}



def _is_divider_page(page: dict) -> bool:
    """章扉だけの情報量の少ないスライドを判定する。"""
    return len(page["content"].strip()) < 100 and page["title"] == page["section"]


def _first_sentence(text: str) -> str:
    """ページ本文の先頭文（句点がない場合は先頭行）を返す。"""
    return re.split(r"(?<=[。！？])|\n", text.strip(), maxsplit=1)[0]


def search_hybrid(query: str) -> dict:
    """ベクトル検索・拡張Graph（next_page/mentions/related_term）・Tree目次を組み合わせる。"""
    if not isinstance(query, str) or not query.strip():
        raise ValueError("検索語が空です。")

    pages = {page["page_id"]: page for page in load_pages(RAG_SOURCE)}
    graph = json.loads(RAG_GRAPH.read_text(encoding="utf-8"))
    nodes = {
        node["id"]: node
        for node in graph["nodes"]
        if node["kind"] == "term" or node["id"] in pages
    }
    adjacent = defaultdict(list)
    for edge in graph["edges"]:
        a, b, rel = edge["source"], edge["target"], edge["relation"]
        if rel in ("next_page", "mentions", "related_term") and a in nodes and b in nodes:
            adjacent[a].append((rel, b))
            adjacent[b].append((rel, a))

    term_degree = {
        tid: sum(1 for rel, nb in adjacent[tid] if rel == "mentions" and nb in pages)
        for tid, node in nodes.items()
        if node["kind"] == "term"
    }

    with open_index(RAG_DB) as db:
        vector = embed_text(query.strip(), "RETRIEVAL_QUERY")
        rows = db.execute(
            """
            SELECT page_id, vec_distance_cosine(embedding, ?) AS distance
            FROM chunks ORDER BY distance, page_id
            """,
            (sqlite_vec.serialize_float32(vector),),
        ).fetchall()

    distances = {row["page_id"]: row["distance"] for row in rows if row["page_id"] in pages}
    ranked = [
        (pid, dist)
        for pid, dist in sorted(distances.items(), key=lambda x: (x[1], x[0]))
        if not _is_divider_page(pages[pid])
    ]
    seed_ids = [pid for pid, _ in ranked[:HYBRID_SEED_K]]
    seed_set = set(seed_ids)
    seed_docs = {pages[pid]["document_id"] for pid in seed_ids}
    seed_sections = {(pages[pid]["document_id"], pages[pid]["section"]) for pid in seed_ids}

    seeds = [
        {
            **pages[pid],
            "distance": distances[pid],
            "hops": 0,
            "path": [{"id": pid, "title": pages[pid]["title"]}],
        }
        for pid in seed_ids
    ]

    candidates: dict[str, list[tuple[str, float, int, list[dict]]]] = defaultdict(list)
    for sid in seed_ids:
        for rel, nb in adjacent[sid]:
            if rel == "next_page" and nb in pages and nb not in seed_set and not _is_divider_page(pages[nb]):
                candidates[nb].append((
                    "next_page",
                    0.08,
                    1,
                    [{"id": sid, "title": pages[sid]["title"]}, {"id": nb, "title": pages[nb]["title"]}],
                ))
            elif rel == "mentions" and nodes[nb]["kind"] == "term":
                deg = max(1, term_degree.get(nb, 1))
                bonus_2hop = 0.06 * min(1.0, 10.0 / deg)
                for rel2, nb2 in adjacent[nb]:
                    if rel2 == "mentions" and nb2 in pages and nb2 not in seed_set and not _is_divider_page(pages[nb2]):
                        candidates[nb2].append((
                            "mentions",
                            bonus_2hop,
                            deg,
                            [
                                {"id": sid, "title": pages[sid]["title"]},
                                {"id": nb, "title": nodes[nb]["title"]},
                                {"id": nb2, "title": pages[nb2]["title"]},
                            ],
                        ))
                    elif rel2 == "related_term" and nodes[nb2]["kind"] == "term":
                        deg2 = max(1, term_degree.get(nb2, 1))
                        bonus_3hop = 0.045 * min(1.0, 10.0 / deg2)
                        for rel3, nb3 in adjacent[nb2]:
                            if rel3 == "mentions" and nb3 in pages and nb3 not in seed_set and not _is_divider_page(pages[nb3]):
                                candidates[nb3].append((
                                    "related_term",
                                    bonus_3hop,
                                    deg2,
                                    [
                                        {"id": sid, "title": pages[sid]["title"]},
                                        {"id": nb, "title": nodes[nb]["title"]},
                                        {"id": nb2, "title": nodes[nb2]["title"]},
                                        {"id": nb3, "title": pages[nb3]["title"]},
                                    ],
                                ))

    scored = []
    for pid, reasons in candidates.items():
        best_eff = -1.0
        best_rel = ""
        best_path: list[dict] = []
        for rel, base_bonus, deg, path in reasons:
            div_bonus = 0.0
            if rel != "next_page" and deg <= 25:
                if pages[pid]["document_id"] not in seed_docs:
                    div_bonus = 0.08
                elif (pages[pid]["document_id"], pages[pid]["section"]) not in seed_sections:
                    div_bonus = 0.04
            eff = base_bonus + div_bonus
            if eff > best_eff:
                best_eff = eff
                best_rel = rel
                best_path = path
        score = (1.0 - distances[pid]) + best_eff
        scored.append((score, pid, best_rel, best_path))

    scored.sort(key=lambda item: (-item[0], item[1]))
    expanded = [
        {
            **pages[pid],
            "distance": distances[pid],
            "score": round(score, 4),
            "relation": rel,
            "hops": len(path) - 1,
            "path": path,
        }
        for score, pid, rel, path in scored[:HYBRID_EXPAND_K]
    ]

    selected_ids = seed_set | {item["page_id"] for item in expanded}
    selected_sections = {(item["document_id"], item["section"]) for item in seeds + expanded}

    tree = json.loads(RAG_TREE.read_text(encoding="utf-8"))
    tree_sections = []
    chapter_index = []
    for doc in tree["children"]:
        if doc["id"] not in RAG_DOCUMENT_IDS:
            continue
        for child in doc.get("children", []):
            if child["kind"] != "section":
                continue
            chapter_index.append(f"{child['id']}: {doc['title']} / {child['title']}")
            if (doc["id"], child["title"]) in selected_sections:
                siblings = []
                for pnode in child.get("children", []):
                    pid = pnode["id"]
                    if pid in selected_ids or pid not in pages:
                        continue
                    page_info = pages[pid]
                    if _is_divider_page(page_info):
                        continue
                    siblings.append(
                        f"{pid} (p.{page_info['page']}) {page_info['title']}: {_first_sentence(page_info['content'])[:60]}"
                    )
                if siblings:
                    tree_sections.append({
                        "section_id": child["id"],
                        "section_title": child["title"],
                        "other_pages": siblings,
                    })

    return {
        "seeds": seeds,
        "expanded": expanded,
        "tree_sections": tree_sections,
        "chapter_index": chapter_index,
    }


def run_full_context(agent, question: str, model, instructions: str, prompt_template: str):
    """全資料を一括投入し、検索ツールを使わずに回答する。"""
    prompt = prompt_template.format(materials=get_all_materials(), question=question)
    print(f"[full-context] 入力{len(prompt):,}文字")
    try:
        with agent.override(tools=[], instructions=instructions):
            return agent.run_sync(prompt, model=model)
    except ModelHTTPError as e:
        print(f"[full-context] APIエラー HTTP {e.status_code}: {e.body}")
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="エージェントを使わず、講義資料の検索だけを実行する")
    parser.add_argument("query", help="調べたい内容")
    args = parser.parse_args()
    for i, result in enumerate(search(args.query), 1):
        print(f"\n{'=' * 60}\n検索結果 {i}\n{'=' * 60}")
        print(f"講義: {result['document_title']}")
        print(f"章: {result['section']}")
        print(f"タイトル: {result['title']}")
        print(f"ページ: {result['page']}")
        print(f"\n{result['content']}")
