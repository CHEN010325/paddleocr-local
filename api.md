# PaddleOCR Local 统一接口

推荐新集成使用 `POST /api/parse`，在 multipart 表单中通过 `modelId` 选择模型：

```bash
curl http://127.0.0.1:8000/api/parse \
  -H "Authorization: Bearer $PANDOCR_API_TOKEN" \
  -F "modelId=pp-ocrv6" \
  -F "fileType=0" \
  -F "file=@document.pdf"
```

可用模型和对应状态：

```bash
curl http://127.0.0.1:8000/api/models
curl http://127.0.0.1:8000/api/model-runtime
```

接口只调用已经启用并就绪的模型；部署和启动可通过 WebUI、CLI 或 `/api/model-runtime/deploy`、`/api/model-runtime/switch` 完成。完整 OpenAPI 快照见 `webui-openapi.json`，运行服务后也可访问 `/docs`。

## PaddleOCR-VL 上游参数参考

以下是服务化部署的 API 参考与多语言服务调用示例：

<details open=""><summary>API 参考</summary>

对于服务提供的主要操作：

* HTTP请求方法为POST。
* 请求体和响应体均为JSON数据（JSON对象）。
* 当请求处理成功时，响应状态码为`200`，响应体的属性如下：


| 名称        | 类型      | 含义                          |
| ----------- | --------- | ----------------------------- |
| `logId`     | `string`  | 请求的UUID。                  |
| `errorCode` | `integer` | 错误码。固定为`0`。           |
| `errorMsg`  | `string`  | 错误说明。固定为`"Success"`。 |
| `result`    | `object`  | 操作结果。                    |

* 当请求处理未成功时，响应体的属性如下：


| 名称        | 类型      | 含义                       |
| ----------- | --------- | -------------------------- |
| `logId`     | `string`  | 请求的UUID。               |
| `errorCode` | `integer` | 错误码。与响应状态码相同。 |
| `errorMsg`  | `string`  | 错误说明。                 |

服务提供的主要操作如下：

* **`infer`**

进行版面解析。

`POST /layout-parsing`

* 请求体的属性如下：


| 名称   | 类型     | 含义                                                                                                                                                                                 | 是否必填 |
| ------ | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | -------- |
| `file` | `string` | 服务器可访问的图像文件或PDF文件的URL，或上述类型文件内容的Base64编码结果。默认对于超过10页的PDF文件，只有前10页的内容会被处理。<br/>要解除页数限制，请在产线配置文件中添加以下配置： |          |

<pre id="__code_22"><nav class="md-code__nav"><button class="md-code__button" title="复制" data-clipboard-target="#__code_22 > code" data-md-type="copy"></button></nav><code>Serving:
  extra:
    max_num_input_imgs: null
</code></pre>

                                                                                                                                                                                                   | 是       |
| `fileType`                  | `integer`｜`null`                              | 文件类型。`0`表示PDF文件，`1`表示图像文件。若请求体无此属性，则将根据URL推断文件类型。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | 否       |
| `useDocOrientationClassify` | `boolean` | `null`                             | 请参阅产线对象中 `predict` 方法的 `use_doc_orientation_classify` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | 否       |
| `useDocUnwarping`           | `boolean` | `null`                             | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `use_doc_unwarping` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | 否       |
| `useLayoutDetection`        | `boolean` | `null`                             | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `use_layout_detection` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | 否       |
| `useChartRecognition`       | `boolean` | `null`                             | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `use_chart_recognition` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | 否       |
| `layoutThreshold`           | `number` | `object` | `null`               | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `layout_threshold` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | 否       |
| `layoutNms`                 | `boolean` | `null`                             | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `layout_nms` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | 否       |
| `layoutUnclipRatio`         | `number` | `array` | `object` | `null` | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `layout_unclip_ratio` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | 否       |
| `layoutMergeBboxesMode`     | `string` | `object` | `null`               | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `layout_merge_bboxes_mode` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | 否       |
| `promptLabel`               | `string` | `null`                              | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `prompt_label` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | 否       |
| `formatBlockContent`        | `boolean` | `null`                             | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `format_block_content` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | 否       |
| `repetitionPenalty`         | `number` | `null`                              | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `repetition_penalty` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | 否       |
| `temperature`               | `number` | `null`                              | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `temperature` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | 否       |
| `topP`                      | `number` | `null`                              | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `top_p` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | 否       |
| `minPixels`                 | `number` | `null`                              | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `min_pixels` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | 否       |
| `maxPixels`                 | `number` | `null`                              | 请参阅PaddleOCR-VL对象中 `predict` 方法的 `max_pixels` 参数相关说明。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | 否       |
| `prettifyMarkdown`          | `boolean`                                          | 是否输出美化后的 Markdown 文本。默认为 `true`。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | 否       |
| `showFormulaNumber`         | `boolean`                                          | 输出的 Markdown 文本中是否包含公式编号。默认为 `false`。                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | 否       |
| `visualize`                 | `boolean` | `null`                             | 是否返回可视化结果图以及处理过程中的中间图像等。* 传入 `true`：返回图像。* 传入 `false`：不返回图像。* 若请求体中未提供该参数或传入 `null`：遵循配置文件`Serving.visualize` 的设置。<br/>例如，在配置文件中添加如下字段：<br/>

<pre id="__code_23"><nav class="md-code__nav"><button class="md-code__button" title="复制" data-clipboard-target="#__code_23 > code" data-md-type="copy"></button></nav><code>Serving:
  visualize: False
</code></pre>

将默认不返回图像，通过请求体中的`visualize`参数可以覆盖默认行为。如果请求体和配置文件中均未设置（或请求体传入`null`、配置文件中未设置），则默认返回图像。 | 否       |

* 请求处理成功时，响应体的`result`具有如下属性：


| 名称                   | 类型     | 含义                                                                                                                                                 |
| ---------------------- | -------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- |
| `layoutParsingResults` | `array`  | 版面解析结果。数组长度为1（对于图像输入）或实际处理的文档页数（对于PDF输入）。对于PDF输入，数组中的每个元素依次表示PDF文件中实际处理的每一页的结果。 |
| `dataInfo`             | `object` | 输入数据信息。                                                                                                                                       |

`layoutParsingResults`中的每个元素为一个`object`，具有如下属性：


| 名称           | 类型     | 含义                                                                                                            |
| -------------- | -------- | --------------------------------------------------------------------------------------------------------------- |
| `prunedResult` | `object` | 对象的`predict` 方法生成结果的 JSON 表示中 `res` 字段的简化版本，其中去除了 `input_path` 和 `page_index` 字段。 |
| `markdown`     | `object` | Markdown结果。                                                                                                  |
| `outputImages` | `object` | `null`                                                                                                          |
| `inputImage`   | `string` | `null`                                                                                                          |

`markdown`为一个`object`，具有如下属性：


| 名称      | 类型      | 含义                                           |
| --------- | --------- | ---------------------------------------------- |
| `text`    | `string`  | Markdown文本。                                 |
| `images`  | `object`  | Markdown图片相对路径和Base64编码图像的键值对。 |
| `isStart` | `boolean` | 当前页面第一个元素是否为段开始。               |
| `isEnd`   | `boolean` | 当前页面最后一个元素是否为段结束。             |

</details>

<details open=""><summary>多语言调用服务示例</summary>

<details open=""><summary>Python</summary>

<pre id="__code_24"><nav class="md-code__nav"><button class="md-code__button" title="复制" data-clipboard-target="#__code_24 > code" data-md-type="copy"></button></nav><code class="language-python">
import base64
import requests
import pathlib

API_URL = "http://localhost:8081/layout-parsing" # 服务URL

image_path = "./demo.jpg"

# 对本地图像进行Base64编码
with open(image_path, "rb") as file:
    image_bytes = file.read()
    image_data = base64.b64encode(image_bytes).decode("ascii")

payload = {
    "file": image_data, # Base64编码的文件内容或者文件URL
    "fileType": 1, # 文件类型，1表示图像文件
}

# 调用API
response = requests.post(API_URL, json=payload)

# 处理接口返回数据
assert response.status_code == 200
result = response.json()["result"]
for i, res in enumerate(result["layoutParsingResults"]):
    print(res["prunedResult"])
    md_dir = pathlib.Path(f"markdown_{i}")
    md_dir.mkdir(exist_ok=True)
    (md_dir / "doc.md").write_text(res["markdown"]["text"])
    for img_path, img in res["markdown"]["images"].items():
        img_path = md_dir / img_path
        img_path.parent.mkdir(parents=True, exist_ok=True)
        img_path.write_bytes(base64.b64decode(img))
    print(f"Markdown document saved at {md_dir / 'doc.md'}")
    for img_name, img in res["outputImages"].items():
        img_path = f"{img_name}_{i}.jpg"
        pathlib.Path(img_path).parent.mkdir(exist_ok=True)
        with open(img_path, "wb") as f:
            f.write(base64.b64decode(img))
        print(f"Output image saved at {img_path}")</code></pre>

</details>

<details><summary>C++</summary>

<pre id="__code_25"><nav class="md-code__nav"><button class="md-code__button" title="复制" data-clipboard-target="#__code_25 > code" data-md-type="copy"></button></nav><code class="language-cpp" tabindex="0">#include <iostream>
#include <filesystem>
#include <fstream>
#include <vector>
#include <string>
#include "cpp-httplib/httplib.h" // https://github.com/Huiyicc/cpp-httplib
#include "nlohmann/json.hpp" // https://github.com/nlohmann/json
#include "base64.hpp" // https://github.com/tobiaslocker/base64

namespace fs = std::filesystem;

int main() {
    httplib::Client client("localhost", 8081);

    const std::string filePath = "./demo.jpg";

    std::ifstream file(filePath, std::ios::binary | std::ios::ate);
    if (!file) {
        std::cerr << "Error opening file: " << filePath << std::endl;
        return 1;
    }

    std::streamsize size = file.tellg();
    file.seekg(0, std::ios::beg);
    std::vector<char> buffer(size);
    if (!file.read(buffer.data(), size)) {
        std::cerr << "Error reading file." << std::endl;
        return 1;
    }

    std::string bufferStr(buffer.data(), static_cast<size_t>(size));
    std::string encodedFile = base64::to_base64(bufferStr);

    nlohmann::json jsonObj;
    jsonObj["file"] = encodedFile;
    jsonObj["fileType"] = 1;

    auto response = client.Post("/layout-parsing", jsonObj.dump(), "application/json");

    if (response && response->status == 200) {
        nlohmann::json jsonResponse = nlohmann::json::parse(response->body);
        auto result = jsonResponse["result"];

        if (!result.is_object() || !result.contains("layoutParsingResults")) {
            std::cerr << "Unexpected response format." << std::endl;
            return 1;
        }

        const auto& results = result["layoutParsingResults"];
        for (size_t i = 0; i < results.size(); ++i) {
            const auto& res = results[i];

            if (res.contains("prunedResult")) {
                std::cout << "Layout result [" << i << "]: " << res["prunedResult"].dump() << std::endl;
            }

            if (res.contains("outputImages") && res["outputImages"].is_object()) {
                for (auto& [imgName, imgBase64] : res["outputImages"].items()) {
                    std::string outputPath = imgName + "_" + std::to_string(i) + ".jpg";
                    fs::path pathObj(outputPath);
                    fs::path parentDir = pathObj.parent_path();
                    if (!parentDir.empty() && !fs::exists(parentDir)) {
                        fs::create_directories(parentDir);
                    }

                    std::string decodedImage = base64::from_base64(imgBase64.get<std::string>());

                    std::ofstream outFile(outputPath, std::ios::binary);
                    if (outFile.is_open()) {
                        outFile.write(decodedImage.c_str(), decodedImage.size());
                        outFile.close();
                        std::cout << "Saved image: " << outputPath << std::endl;
                    } else {
                        std::cerr << "Failed to save image: " << outputPath << std::endl;
                    }
                }
            }
        }
    } else {
        std::cerr << "Request failed." << std::endl;
        if (response) {
            std::cerr << "HTTP status: " << response->status << std::endl;
            std::cerr << "Response body: " << response->body << std::endl;
        }
        return 1;
    }

    return 0;
}
</std::string></size_t></char></code></pre>

</details>

<details><summary>Java</summary>

<pre id="__code_26"><nav class="md-code__nav"><button class="md-code__button" title="复制" data-clipboard-target="#__code_26 > code" data-md-type="copy"></button></nav><code class="language-java" tabindex="0">import okhttp3.*;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.node.ObjectNode;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.util.Base64;
import java.nio.file.Paths;
import java.nio.file.Files;

public class Main {
    public static void main(String[] args) throws IOException {
        String API_URL = "http://localhost:8081/layout-parsing";
        String imagePath = "./demo.jpg";

        File file = new File(imagePath);
        byte[] fileContent = java.nio.file.Files.readAllBytes(file.toPath());
        String base64Image = Base64.getEncoder().encodeToString(fileContent);

        ObjectMapper objectMapper = new ObjectMapper();
        ObjectNode payload = objectMapper.createObjectNode();
        payload.put("file", base64Image);
        payload.put("fileType", 1);

        OkHttpClient client = new OkHttpClient();
        MediaType JSON = MediaType.get("application/json; charset=utf-8");

        RequestBody body = RequestBody.create(JSON, payload.toString());

        Request request = new Request.Builder()
                .url(API_URL)
                .post(body)
                .build();

        try (Response response = client.newCall(request).execute()) {
            if (response.isSuccessful()) {
                String responseBody = response.body().string();
                JsonNode root = objectMapper.readTree(responseBody);
                JsonNode result = root.get("result");

                JsonNode layoutParsingResults = result.get("layoutParsingResults");
                for (int i = 0; i < layoutParsingResults.size(); i++) {
                    JsonNode item = layoutParsingResults.get(i);
                    int finalI = i;
                    JsonNode prunedResult = item.get("prunedResult");
                    System.out.println("Pruned Result [" + i + "]: " + prunedResult.toString());

                    JsonNode outputImages = item.get("outputImages");
                    outputImages.fieldNames().forEachRemaining(imgName -> {
                        try {
                            String imgBase64 = outputImages.get(imgName).asText();
                            byte[] imgBytes = Base64.getDecoder().decode(imgBase64);
                            String imgPath = imgName + "_" + finalI + ".jpg";

                            File outputFile = new File(imgPath);
                            File parentDir = outputFile.getParentFile();
                            if (parentDir != null && !parentDir.exists()) {
                                parentDir.mkdirs();
                                System.out.println("Created directory: " + parentDir.getAbsolutePath());
                            }

                            try (FileOutputStream fos = new FileOutputStream(outputFile)) {
                                fos.write(imgBytes);
                                System.out.println("Saved image: " + imgPath);
                            }
                        } catch (IOException e) {
                            System.err.println("Failed to save image: " + e.getMessage());
                        }
                    });
                }
            } else {
                System.err.println("Request failed with HTTP code: " + response.code());
            }
        }
    }
}
</code></pre>

</details>

<details><summary>Go</summary>

<pre id="__code_27"><nav class="md-code__nav"><button class="md-code__button" title="复制" data-clipboard-target="#__code_27 > code" data-md-type="copy"></button></nav><code class="language-go">package main

import (
    "bytes"
    "encoding/base64"
    "encoding/json"
    "fmt"
    "io/ioutil"
    "net/http"
    "os"
    "path/filepath"
)

func main() {
    API_URL := "http://localhost:8081/layout-parsing"
    filePath := "./demo.jpg"

    fileBytes, err := ioutil.ReadFile(filePath)
    if err != nil {
        fmt.Printf("Error reading file: %v\n", err)
        return
    }
    fileData := base64.StdEncoding.EncodeToString(fileBytes)

    payload := map[string]interface{}{
        "file":     fileData,
        "fileType": 1,
    }
    payloadBytes, err := json.Marshal(payload)
    if err != nil {
        fmt.Printf("Error marshaling payload: %v\n", err)
        return
    }

    client := &http.Client{}
    req, err := http.NewRequest("POST", API_URL, bytes.NewBuffer(payloadBytes))
    if err != nil {
        fmt.Printf("Error creating request: %v\n", err)
        return
    }
    req.Header.Set("Content-Type", "application/json")

    res, err := client.Do(req)
    if err != nil {
        fmt.Printf("Error sending request: %v\n", err)
        return
    }
    defer res.Body.Close()

    if res.StatusCode != http.StatusOK {
        fmt.Printf("Unexpected status code: %d\n", res.StatusCode)
        return
    }

    body, err := ioutil.ReadAll(res.Body)
    if err != nil {
        fmt.Printf("Error reading response: %v\n", err)
        return
    }

    type Markdown struct {
        Text   string            `json:"text"`
        Images map[string]string `json:"images"`
    }

    type LayoutResult struct {
        PrunedResult map[string]interface{} `json:"prunedResult"`
        Markdown     Markdown               `json:"markdown"`
        OutputImages map[string]string      `json:"outputImages"`
        InputImage   *string                `json:"inputImage"`
    }

    type Response struct {
        Result struct {
            LayoutParsingResults []LayoutResult `json:"layoutParsingResults"`
            DataInfo             interface{}    `json:"dataInfo"`
        } `json:"result"`
    }

    var respData Response
    if err := json.Unmarshal(body, &respData); err != nil {
        fmt.Printf("Error parsing response: %v\n", err)
        return
    }

    for i, res := range respData.Result.LayoutParsingResults {
        fmt.Printf("Result %d - prunedResult: %+v\n", i, res.PrunedResult)

        mdDir := fmt.Sprintf("markdown_%d", i)
        os.MkdirAll(mdDir, 0755)
        mdFile := filepath.Join(mdDir, "doc.md")
        if err := os.WriteFile(mdFile, []byte(res.Markdown.Text), 0644); err != nil {
            fmt.Printf("Error writing markdown file: %v\n", err)
        } else {
            fmt.Printf("Markdown document saved at %s\n", mdFile)
        }

        for path, imgBase64 := range res.Markdown.Images {
            fullPath := filepath.Join(mdDir, path)
            if err := os.MkdirAll(filepath.Dir(fullPath), 0755); err != nil {
                fmt.Printf("Error creating directory for markdown image: %v\n", err)
                continue
            }
            imgBytes, err := base64.StdEncoding.DecodeString(imgBase64)
            if err != nil {
                fmt.Printf("Error decoding markdown image: %v\n", err)
                continue
            }
            if err := os.WriteFile(fullPath, imgBytes, 0644); err != nil {
                fmt.Printf("Error saving markdown image: %v\n", err)
            }
        }

        for name, imgBase64 := range res.OutputImages {
            imgBytes, err := base64.StdEncoding.DecodeString(imgBase64)
            if err != nil {
                fmt.Printf("Error decoding output image %s: %v\n", name, err)
                continue
            }
            filename := fmt.Sprintf("%s_%d.jpg", name, i)

            if err := os.MkdirAll(filepath.Dir(filename), 0755); err != nil {
                fmt.Printf("Error creating directory for output image: %v\n", err)
                continue
            }

            if err := os.WriteFile(filename, imgBytes, 0644); err != nil {
                fmt.Printf("Error saving output image %s: %v\n", filename, err)
            } else {
                fmt.Printf("Output image saved at %s\n", filename)
            }
        }
    }
}
</code></pre>

</details>

<details><summary>C#</summary>

<pre id="__code_28"><nav class="md-code__nav"><button class="md-code__button" title="复制" data-clipboard-target="#__code_28 > code" data-md-type="copy"></button></nav><code class="language-csharp" tabindex="0">using System;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Threading.Tasks;
using Newtonsoft.Json.Linq;

class Program
{
    static readonly string API_URL = "http://localhost:8081/layout-parsing";
    static readonly string inputFilePath = "./demo.jpg";

    static async Task Main(string[] args)
    {
        var httpClient = new HttpClient();

        byte[] fileBytes = File.ReadAllBytes(inputFilePath);
        string fileData = Convert.ToBase64String(fileBytes);

        var payload = new JObject
        {
            { "file", fileData },
            { "fileType", 1 }
        };
        var content = new StringContent(payload.ToString(), Encoding.UTF8, "application/json");

        HttpResponseMessage response = await httpClient.PostAsync(API_URL, content);
        response.EnsureSuccessStatusCode();

        string responseBody = await response.Content.ReadAsStringAsync();
        JObject jsonResponse = JObject.Parse(responseBody);

        JArray layoutParsingResults = (JArray)jsonResponse["result"]["layoutParsingResults"];
        for (int i = 0; i < layoutParsingResults.Count; i++)
        {
            var res = layoutParsingResults[i];
            Console.WriteLine($"[{i}] prunedResult:\n{res["prunedResult"]}");

            JObject outputImages = res["outputImages"] as JObject;
            if (outputImages != null)
            {
                foreach (var img in outputImages)
                {
                    string imgName = img.Key;
                    string base64Img = img.Value?.ToString();
                    if (!string.IsNullOrEmpty(base64Img))
                    {
                        string imgPath = $"{imgName}_{i}.jpg";
                        byte[] imageBytes = Convert.FromBase64String(base64Img);

                        string directory = Path.GetDirectoryName(imgPath);
                        if (!string.IsNullOrEmpty(directory) && !Directory.Exists(directory))
                        {
                            Directory.CreateDirectory(directory);
                            Console.WriteLine($"Created directory: {directory}");
                        }

                        File.WriteAllBytes(imgPath, imageBytes);
                        Console.WriteLine($"Output image saved at {imgPath}");
                    }
                }
            }
        }
    }
}
</code></pre>

</details>

<details><summary>Node.js</summary>

<pre id="__code_29"><nav class="md-code__nav"><button class="md-code__button" title="复制" data-clipboard-target="#__code_29 > code" data-md-type="copy"></button></nav><code class="language-js">const axios = require('axios');
const fs = require('fs');
const path = require('path');

const API_URL = 'http://localhost:8081/layout-parsing';
const imagePath = './demo.jpg';
const fileType = 1;

function encodeImageToBase64(filePath) {
  const bitmap = fs.readFileSync(filePath);
  return Buffer.from(bitmap).toString('base64');
}

const payload = {
  file: encodeImageToBase64(imagePath),
  fileType: fileType
};

axios.post(API_URL, payload)
  .then(response => {
    const results = response.data.result.layoutParsingResults;
    results.forEach((res, index) => {
      console.log(`\n[${index}] prunedResult:`);
      console.log(res.prunedResult);

      const outputImages = res.outputImages;
      if (outputImages) {
        Object.entries(outputImages).forEach(([imgName, base64Img]) => {
          const imgPath = `${imgName}_${index}.jpg`;

          const directory = path.dirname(imgPath);
          if (!fs.existsSync(directory)) {
            fs.mkdirSync(directory, { recursive: true });
            console.log(`Created directory: ${directory}`);
          }

          fs.writeFileSync(imgPath, Buffer.from(base64Img, 'base64'));
          console.log(`Output image saved at ${imgPath}`);
        });
      } else {
        console.log(`[${index}] No outputImages.`);
      }
    });
  })
  .catch(error => {
    console.error('Error during API request:', error.message || error);
  });
</code></pre>

</details>

<details><summary>PHP</summary>

<pre id="__code_30"><nav class="md-code__nav"><button class="md-code__button" title="复制" data-clipboard-target="#__code_30 > code" data-md-type="copy"></button></nav><code class="language-php"><?php

$API_URL = "http://localhost:8081/layout-parsing";
$image_path = "./demo.jpg";

$image_data = base64_encode(file_get_contents($image_path));
$payload = array("file" => $image_data, "fileType" => 1);

$ch = curl_init($API_URL);
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($payload));
curl_setopt($ch, CURLOPT_HTTPHEADER, array('Content-Type: application/json'));
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
$response = curl_exec($ch);
curl_close($ch);

$result = json_decode($response, true)["result"]["layoutParsingResults"];

foreach ($result as $i => $item) {
    echo "[$i] prunedResult:\n";
    print_r($item["prunedResult"]);

    if (!empty($item["outputImages"])) {
        foreach ($item["outputImages"] as $img_name => $img_base64) {
            $output_image_path = "{$img_name}_{$i}.jpg";

            $directory = dirname($output_image_path);
            if (!is_dir($directory)) {
                mkdir($directory, 0777, true);
                echo "Created directory: $directory\n";
            }

            file_put_contents($output_image_path, base64_decode($img_base64));
            echo "Output image saved at $output_image_path\n";
        }
    } else {
        echo "No outputImages found for item $i\n";
    }
}
?>
</code></pre>

</details>

</details>

### 4.4 产线配置调整说明[¶](https://www.paddleocr.ai/latest/version3.x/pipeline_usage/PaddleOCR-VL.html#44 "Permanent link")

> 若您无需调整产线配置，可忽略此小节。

调整服务化部署的 PaddleOCR-VL 配置只需以下三步：

1. 生成配置文件
2. 修改配置文件
3. 应用配置文件

#### 4.4.1 生成配置文件[¶](https://www.paddleocr.ai/latest/version3.x/pipeline_usage/PaddleOCR-VL.html#441 "Permanent link")

```
paddlex --get_pipeline_config PaddleOCR-VL
```
#### 4.4.2 修改配置文件[¶](https://www.paddleocr.ai/latest/version3.x/pipeline_usage/PaddleOCR-VL.html#442 "Permanent link")

**使用加速框架提升 VLM 推理性能**

如需使用 vLLM 等加速框架提升 VLM 推理性能（第 2 节详细介绍如何启动 VLM 推理服务），可在产线配置文件中修改 `VLRecognition.genai_config.backend` 和 `VLRecognition.genai_config.server_url` 字段，例如：

```
VLRecognition:
  ...
  genai_config:
    backend: vllm-server
    server_url: http://127.0.0.1:8118/v1
```
**启用文档图像预处理功能**

默认配置启动的服务不支持文档预处理功能。若客户端调用该功能，将返回错误信息。如需启用文档预处理，请在产线配置文件中将 `use_doc_preprocessor` 设置为 `True`，并使用修改后的配置文件启动服务。

**禁用结果可视化功能**

服务默认返回可视化结果，这会引入额外开销。如需禁用该功能，可在产线配置文件中添加如下配置：

```
Serving:
  visualize: False
```
此外，也可在请求体中设置 `visualize` 字段为 `false`，以针对单次请求禁用可视化。

**配置返回图像 URL**

对于可视化结果图及 Markdown 中包含的图像，服务默认以 Base64 编码返回。如需以 URL 形式返回图像，可在产线配置文件中添加如下配置：

```
Serving:
  extra:
    file_storage:
      type: bos
      endpoint: https://bj.bcebos.com
      bucket_name: some-bucket
      ak: xxx
      sk: xxx
      key_prefix: deploy
    return_img_urls: True
```
目前支持将生成的图像存储至百度智能云对象存储（BOS）并返回 URL。相关参数说明如下：

* `endpoint`：访问域名，必须配置。
* `ak`：百度智能云 AK，必须配置。
* `sk`：百度智能云 SK，必须配置。
* `bucket_name`：存储空间名称，必须配置。
* `key_prefix`：Object key 的统一前缀。
* `connection_timeout_in_mills`：请求超时时间（单位：毫秒）。

有关 AK/SK 获取等更多信息，请参考 [百度智能云官方文档](https://cloud.baidu.com/doc/BOS/index.html)。

**修改 PDF 解析页数限制**

出于性能考虑，服务默认仅处理接收到的 PDF 文件的前 10 页。如需调整页数限制，可在产线配置文件中添加如下配置：

```
Serving:
  extra:
    max_num_input_imgs: <新的页数限制，例如 100>
```
将 `max_num_input_imgs` 设置为 `null` 可解除页数限制。

#### 4.4.3 应用配置文件[¶](https://www.paddleocr.ai/latest/version3.x/pipeline_usage/PaddleOCR-VL.html#443 "Permanent link")

**若您是 Docker Compose 部署：**

将自定义的产线配置文件覆盖至 `ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-vl`（或对应容器）中的 `/home/paddleocr/pipeline_config.yaml`。

**若您是手动安装依赖部署：**

将 `--pipeline` 参数指定为自定义配置文件路径。

## 5. 模型微调[¶](https://www.paddleocr.ai/latest/version3.x/pipeline_usage/PaddleOCR-VL.html#5 "Permanent link")

若您发现 PaddleOCR-VL 在特定业务场景中的精度表现未达预期，我们推荐使用 [ERNIEKit 套件](https://github.com/PaddlePaddle/ERNIE/tree/release/v1.4) 对 PaddleOCR-VL-0.9B 模型进行有监督微调（SFT）。具体操作步骤可参考 [ERNIEKit 官方文档](https://github.com/PaddlePaddle/ERNIE/blob/release/v1.4/docs/paddleocr_vl_sft_zh.md)。

> 目前暂不支持对版面检测排序模型进行微调。

## 评论

<iframe class="giscus-frame" title="Comments" scrolling="no" allow="clipboard-write" src="https://giscus.app/en/widget?origin=https%3A%2F%2Fwww.paddleocr.ai%2Flatest%2Fversion3.x%2Fpipeline_usage%2FPaddleOCR-VL.html&session=&theme=light&reactionsEnabled=1&emitMetadata=0&inputPosition=top&repo=PaddlePaddle%2FPaddleOCR&repoId=MDEwOlJlcG9zaXRvcnkyNjIyOTYxMjI%3D&category=Q%26A&categoryId=DIC_kwDOD6JSOs4COrbO&strict=0&description=Awesome+multilingual+OCR+toolkits+based+on+PaddlePaddle+%28practical+ultra+lightweight+OCR+system%2C+support+80%2B+languages+recognition%2C+provide+data+annotation+and+synthesis+tools%2C+support+training+and+deployment+among+server%2C+mobile%2C+embedded+and+IoT+devices%29&backLink=https%3A%2F%2Fwww.paddleocr.ai%2Flatest%2Fversion3.x%2Fpipeline_usage%2FPaddleOCR-VL.html&term=latest%2Fversion3.x%2Fpipeline_usage%2FPaddleOCR-VL" loading="lazy"></iframe>

回到页面顶部[上一页PP-ChatOCRv4简介](https://www.paddleocr.ai/latest/version3.x/algorithm/PP-ChatOCRv4/PP-ChatOCRv4.html)[下一页PaddleOCR-VL简介](https://www.paddleocr.ai/latest/version3.x/algorithm/PaddleOCR-VL/PaddleOCR-VL.html)Copyright © 2024 Maintained by PaddleOCR PMC.

Made with [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/)

[](https://github.com/PaddlePaddle/PaddleOCR "github.com")[](https://pypi.org/project/paddleocr/ "pypi.org")
