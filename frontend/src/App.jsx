import { useEffect, useState } from "react";

const api = (path, opts) => fetch(`/api${path}`, opts).then((r) => r.json());

export default function App() {
  const [todos, setTodos] = useState([]);
  const [text, setText] = useState("");

  const load = () => api("/todos").then(setTodos);
  useEffect(() => { load(); }, []);

  const add = async (e) => {
    e.preventDefault();
    if (!text.trim()) return;
    await api("/todos", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text }),
    });
    setText("");
    load();
  };

  const toggle = (id) => api(`/todos/${id}/toggle`, { method: "POST" }).then(load);
  const remove = (id) => api(`/todos/${id}`, { method: "DELETE" }).then(load);

  return (
    <main style={{ maxWidth: 480, margin: "3rem auto", fontFamily: "system-ui" }}>
      <h1>some_sim todos</h1>
      <form onSubmit={add}>
        <input value={text} onChange={(e) => setText(e.target.value)} placeholder="What needs doing?" />
        <button>Add</button>
      </form>
      <ul>
        {todos.map((t) => (
          <li key={t.id}>
            <label style={{ textDecoration: t.done ? "line-through" : "none" }}>
              <input type="checkbox" checked={t.done} onChange={() => toggle(t.id)} /> {t.text}
            </label>{" "}
            <button onClick={() => remove(t.id)}>✕</button>
          </li>
        ))}
      </ul>
    </main>
  );
}
