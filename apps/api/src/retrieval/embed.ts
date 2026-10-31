export const EMBEDDING_DIM = 256;

export interface Embedder {
  readonly name: string;
  readonly dim: number;
  embed(texts: string[]): Promise<number[][]>;
}

const STOPWORDS = new Set(
  "a an and are as at be best by can do for from good how i in is it me my of on or our some the things to us we what where which with you your".split(" "),
);

export function tokenize(text: string): string[] {
  return text
    .toLowerCase()
    .normalize("NFKD")
    .replace(/[^a-z0-9 ]+/g, " ")
    .split(/\s+/)
    .filter((t) => t.length > 1 && !STOPWORDS.has(t))
    .map((t) => (t.length > 3 && t.endsWith("s") && !t.endsWith("ss") ? t.slice(0, -1) : t));
}

function fnv1a(s: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return h >>> 0;
}

/**
 * Feature-hashed bag of words and bigrams. No network and no key, so tests,
 * evals and the offline demo all run anywhere. Swap in VoyageEmbedder for
 * real semantic similarity.
 */
export class HashedEmbedder implements Embedder {
  readonly name = "hashed-v1";
  readonly dim = EMBEDDING_DIM;

  async embed(texts: string[]): Promise<number[][]> {
    return texts.map((t) => this.embedOne(t));
  }

  embedOne(text: string): number[] {
    const v = new Array<number>(this.dim).fill(0);
    const tokens = tokenize(text);
    const features = [...tokens, ...tokens.slice(1).map((t, i) => `${tokens[i]}_${t}`)];
    for (const f of features) {
      const h = fnv1a(f);
      const sign = h & 1 ? 1 : -1;
      const idx = (h >>> 1) % this.dim;
      v[idx] = (v[idx] ?? 0) + sign * (f.includes("_") ? 0.5 : 1);
    }
    return normalize(v);
  }
}

export class VoyageEmbedder implements Embedder {
  readonly name = "voyage-3.5-lite";
  readonly dim = EMBEDDING_DIM;

  constructor(private readonly apiKey: string) {}

  async embed(texts: string[]): Promise<number[][]> {
    const res = await fetch("https://api.voyageai.com/v1/embeddings", {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${this.apiKey}` },
      body: JSON.stringify({ input: texts, model: "voyage-3.5-lite", output_dimension: this.dim }),
    });
    if (!res.ok) throw new Error(`Voyage embeddings failed: ${res.status}`);
    const body = (await res.json()) as { data: { embedding: number[] }[] };
    return body.data.map((d) => normalize(d.embedding));
  }
}

export function normalize(v: number[]): number[] {
  const norm = Math.sqrt(v.reduce((s, x) => s + x * x, 0)) || 1;
  return v.map((x) => x / norm);
}

export function cosine(a: number[], b: number[]): number {
  let s = 0;
  for (let i = 0; i < a.length; i++) s += (a[i] ?? 0) * (b[i] ?? 0);
  return s;
}

export function createEmbedder(): Embedder {
  const key = process.env.VOYAGE_API_KEY;
  return key ? new VoyageEmbedder(key) : new HashedEmbedder();
}
