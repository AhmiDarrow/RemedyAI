//! Search over the offline manual. Same weights as `desktop/src/help/catalog.ts`.

use super::catalog::{articles, HelpArticle};

/// Chapters that mention `query`, best match first.
///
/// An empty query returns every chapter in catalog order. Each term scores the
/// title (+8), a tag (+5), the summary (+3), and the haystack (+1). The
/// haystack is title, summary, category, tags, and the first 4 000 characters
/// of the body.
pub(crate) fn search_articles(query: &str) -> Vec<&'static HelpArticle> {
    let q = query.trim().to_lowercase();
    if q.is_empty() {
        return articles().iter().collect();
    }
    let terms: Vec<&str> = q.split_whitespace().filter(|t| !t.is_empty()).collect();
    let mut scored: Vec<(&HelpArticle, i32)> = articles()
        .iter()
        .filter_map(|article| {
            let mut score = 0;
            let title = article.title.to_lowercase();
            let summary = article.summary.to_lowercase();
            // JS `slice(0, 4000)` is 4 000 code units. These chapters are BMP.
            let body_cut: String = article.body.chars().take(4000).collect();
            let mut hay = String::new();
            hay.push_str(&title);
            hay.push('\n');
            hay.push_str(&summary);
            hay.push('\n');
            hay.push_str(&article.category.to_lowercase());
            hay.push('\n');
            for (i, tag) in article.tags.iter().enumerate() {
                if i > 0 {
                    hay.push(' ');
                }
                hay.push_str(&tag.to_lowercase());
            }
            hay.push('\n');
            hay.push_str(&body_cut.to_lowercase());
            for term in &terms {
                if title.contains(term) {
                    score += 8;
                }
                if article.tags.iter().any(|tag| tag.to_lowercase().contains(term)) {
                    score += 5;
                }
                if summary.contains(term) {
                    score += 3;
                }
                if hay.contains(term) {
                    score += 1;
                }
            }
            (score > 0).then_some((article, score))
        })
        .collect();
    scored.sort_by(|a, b| b.1.cmp(&a.1).then_with(|| a.0.order.total_cmp(&b.0.order)));
    scored.into_iter().map(|(article, _)| article).collect()
}

#[cfg(test)]
mod tests {
    use super::super::catalog::articles;
    use super::search_articles;

    #[test]
    fn scores_like_the_tauri_catalog() {
        let oauth: Vec<_> = search_articles("oauth xai").iter().map(|article| article.id).collect();
        assert!(oauth.contains(&"03-providers-and-auth"), "{oauth:?}");
        let defender: Vec<_> = search_articles("defender").iter().map(|article| article.id).collect();
        assert!(defender.contains(&"09-troubleshooting"), "{defender:?}");
        assert_eq!(search_articles("   ").len(), articles().len());
    }
}
