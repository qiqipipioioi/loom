# Name, feed URL, category, polling interval in minutes, request headers.
MARKET_FEEDS: tuple[tuple[str, str, str, int, dict[str, str]], ...] = (
    (
        "GlobeNewswire · 上市公司公告",
        "https://rss.globenewswire.com/RssFeed/orgclass/1/feedTitle/"
        "GlobeNewswire%20-%20News%20about%20Public%20Companies",
        "宏观",
        1,
        {},
    ),
    (
        "Business Wire · 人事变动",
        "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFxRXA==",
        "宏观",
        2,
        {},
    ),
    (
        "Business Wire · 并购",
        "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEFtRWA==",
        "宏观",
        2,
        {},
    ),
    (
        "Business Wire · 财报",
        "https://feed.businesswire.com/rss/home/?rss=G1QFDERJXkJeEF9YXA==",
        "宏观",
        3,
        {},
    ),
    (
        "PR Newswire · 金融与投资",
        "https://www.prnewswire.com/rss/financial-services-latest-news/"
        "financial-services-latest-news-list.rss",
        "宏观",
        2,
        {},
    ),
    (
        "SEC EDGAR · 8-K",
        "https://www.sec.gov/cgi-bin/browse-edgar?"
        "action=getcurrent&company=&count=100&dateb=&output=atom&owner=exclude&start=0&type=8-K",
        "宏观",
        1,
        {"User-Agent": "Loom/0.1 public filing reader"},
    ),
    ("SEC · 新闻稿", "https://www.sec.gov/news/pressreleases.rss", "宏观", 2, {}),
    ("美联储 · 货币政策", "https://www.federalreserve.gov/feeds/press_monetary.xml", "宏观", 1, {}),
    (
        "美联储 · 讲话与证词",
        "https://www.federalreserve.gov/feeds/speeches_and_testimony.xml",
        "宏观",
        2,
        {},
    ),
    ("美国 BLS · CPI", "https://www.bls.gov/feed/cpi.rss", "宏观", 2, {}),
    ("美国 BLS · 就业报告", "https://www.bls.gov/feed/empsit.rss", "宏观", 2, {}),
    ("美国 BLS · PPI", "https://www.bls.gov/feed/ppi.rss", "宏观", 2, {}),
    ("美国 BEA · 经济数据发布", "https://www.bea.gov/news/rss", "宏观", 2, {}),
    ("白宫 · 总统行动", "https://www.whitehouse.gov/presidential-actions/feed/", "时政", 2, {}),
    ("白宫 · 政策简报", "https://www.whitehouse.gov/fact-sheets/feed/", "时政", 2, {}),
    (
        "美国财政部 · 国债发行公告",
        "https://www.treasurydirect.gov/TA_WS/securities/announced/rss",
        "宏观",
        2,
        {},
    ),
    (
        "美国财政部 · 国债拍卖结果",
        "https://www.treasurydirect.gov/TA_WS/securities/auctioned/rss",
        "宏观",
        2,
        {},
    ),
    ("NVIDIA · 官方新闻稿", "https://nvidianews.nvidia.com/cats/press_release.xml", "科技", 1, {}),
    ("Trump’s Truth · 特朗普帖子（第三方存档）", "https://www.trumpstruth.org/feed", "时政", 2, {}),
)
