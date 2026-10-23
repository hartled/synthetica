// Live-feed decisions for the home page. No DOM access.
// The page keeps the earlier scroll restoration and calls planLivePosts
// before it rewrites the feed.
(function (root) {
  var NEAR_TOP = 200;
  // Newest card still in the upper viewport: the reader is caught up.
  var CARD_NEAR = 280;

  function isNearTop(scrollY, firstCardTop) {
    if ((Number(scrollY) || 0) <= NEAR_TOP) return true;
    if (typeof firstCardTop !== "number" || !isFinite(firstCardTop)) return false;
    return firstCardTop >= -8 && firstCardTop <= CARD_NEAR;
  }

  function planLivePosts(opts) {
    opts = opts || {};
    var posts = Array.isArray(opts.posts) ? opts.posts : [];
    var shown = Array.isArray(opts.shownIds) ? opts.shownIds : [];
    var shownSet = Object.create(null);
    for (var i = 0; i < shown.length; i++) shownSet[String(shown[i])] = 1;
    if (opts.firstLoad || opts.reveal) {
      return {
        mode: opts.reveal ? "reveal" : "replace",
        posts: posts,
        buffered: [],
        anchor: false,
        jumpTop: !!opts.reveal
      };
    }
    if (isNearTop(opts.scrollY, opts.firstCardTop)) {
      return { mode: "insert", posts: posts, buffered: [], anchor: true, jumpTop: false };
    }
    var kept = [];
    var fresh = [];
    for (var j = 0; j < posts.length; j++) {
      var post = posts[j];
      if (post && shownSet[String(post.id)]) kept.push(post);
      else fresh.push(post);
    }
    if (!fresh.length) {
      return { mode: "refresh", posts: posts, buffered: [], anchor: true, jumpTop: false };
    }
    if (!kept.length) {
      return { mode: "hold-stale", posts: null, buffered: fresh, anchor: true, jumpTop: false };
    }
    return { mode: "hold", posts: kept, buffered: fresh, anchor: true, jumpTop: false };
  }

  function newPostsLabel(n) {
    n = Number(n) || 0;
    if (n === 1) return "1 new post";
    return n + " new posts";
  }

  var api = {
    NEAR_TOP: NEAR_TOP,
    CARD_NEAR: CARD_NEAR,
    isNearTop: isNearTop,
    planLivePosts: planLivePosts,
    newPostsLabel: newPostsLabel
  };
  root.planLivePosts = planLivePosts;
  root.newPostsLabel = newPostsLabel;
  root.isNearTop = isNearTop;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof window !== "undefined" ? window : globalThis);
