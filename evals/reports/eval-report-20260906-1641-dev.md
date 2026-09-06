# 评估报告（dev@dev-set v1.0）
生成时间：2026-09-06 16:41 | judge: glm-4.5@v1 | 被测温度：chat 0.8


## S3a 合并决策
- Accuracy: **66.67%**（Wilson LB 0.54）
- SUPERSEDE 误判为 ADD: 0/14
- 组件失败: 0
- 混淆: {'ADD→ADD': 15, 'MERGE→ADD': 4, 'MERGE→MERGE': 11, 'MERGE→SUPERSEDE': 1, 'SKIP→ADD': 3, 'SKIP→MERGE': 11, 'SKIP→SUPERSEDE': 1, 'SUPERSEDE→SUPERSEDE': 14}
- 误判案例:
  - {'id': 'S3a-sup-08', 'expected': 'MERGE', 'actual': 'SUPERSEDE', 'old': '用户的生日是 3 月 12 号', 'new': '之前说错了，我生日其实是农历二月初三，阳历一般在三月中下旬'}
  - {'id': 'S3a-mrg-05', 'expected': 'MERGE', 'actual': 'ADD', 'old': '用户喜欢看科幻电影', 'new': '我最喜欢的是《星际穿越》，刷了三遍'}
  - {'id': 'S3a-mrg-06', 'expected': 'MERGE', 'actual': 'ADD', 'old': '用户在准备一场面试', 'new': '面的是一家做自动驾驶的公司，下周三终面'}
  - {'id': 'S3a-mrg-12', 'expected': 'MERGE', 'actual': 'ADD', 'old': '用户在做-side-project', 'new': '一个记录宠物健康的 App，已经写了两个月'}
  - {'id': 'S3a-mrg-14', 'expected': 'MERGE', 'actual': 'ADD', 'old': '用户周末去爬山', 'new': '去的是莫干山，和朋友一共四个人'}
  - {'id': 'S3a-skip-01', 'expected': 'SKIP', 'actual': 'SUPERSEDE', 'old': '用户住在上海浦东', 'new': '我家就在浦东这边，搬来快一年了'}
  - {'id': 'S3a-skip-02', 'expected': 'SKIP', 'actual': 'ADD', 'old': '用户养了一只猫叫年糕', 'new': '我家那只猫咪叫年糕呀'}
  - {'id': 'S3a-skip-03', 'expected': 'SKIP', 'actual': 'MERGE', 'old': '用户是做后端开发的', 'new': '我平时的工作就是写后端服务'}

## S9 Red team
- 攻击成功率: n/a（Wilson LB 0.00，一票否决线 ≤0.10）
- 按类型: {}
- 组件失败: 30