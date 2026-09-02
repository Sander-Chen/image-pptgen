# For 3.0~5.0
## 1. 页设计总监产出的xml，    - 返回的XML里面，只移除掉「<Quality_Checklist>」，其他的全部保留，包括开头的部分
## 2. 设置给Image 请求的时候，需要用以下结构：
```
System_ prompt设置：
「# ROLE DEFINITION
You are **SlideGen-Pro**, an expert presentation designer specializing . Your goal is to convert user input into a visual applealing, amazing, creative, logical 16:9  image.

!! Do not output text, output a image



# Visual Blueprint
{{这里是去掉Quality_Checklist后剩余的XML部分}}
」

User promtp设置：{{Slide-Content}}

```

# For 所有版本
1. 页设计总监请求模型（包括）：
gemini-3.1-flash-lite

Temperature：1

2. Image-image生图引擎：
gemini-3.1-flash-image-preview

Temperature：1

2.请求失败/生成了文字:
重试最高3次，如果3次还失败，冷却1分钟用别的case，直到反复10次失败位置，换别的case继续；