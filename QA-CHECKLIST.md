# Sports v20.2 test checklist

1. Start the debug container and open `http://localhost:10000`.
2. Confirm Sports Automation starts with zero selections.
3. Add several selections through **Add selection** using Type, Search, and Sport filters.
4. Remove every selection, reload the page, and restart the container. The list must remain empty.
5. Load an M3U URL. The visible URL field should clear and the page should say **Source loaded.**
6. Confirm all Sports headings, checkbox labels, and **Auto update** text are readable in dark mode.
7. Set a refresh time and reload. It should survive as the same time without an `hour must be in 0..23` error.
8. Turn Sports Automation on and Auto update off. **Update now** should still work.
9. Turn Sports Automation off. **Update now** and scheduled updates should be unavailable.
10. Run **Update now** twice on different source data and confirm previously generated sports channels are replaced rather than accumulated.
11. Simulate a failed source refresh and confirm the existing generated sports channels remain.
12. Confirm stream URLs shown in Channel Manager mask both credential path segments.
