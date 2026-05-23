class Dedup < Formula
  desc "Local browser UI for reviewing and trashing duplicate files"
  homepage "https://github.com/pathanin"
  license "MIT"

  depends_on "python@3.12"

  def install
    libexec.install tap.path/"dedup.py"

    (bin/"dedup").write <<~EOS
      #!/bin/bash
      exec "#{Formula["python@3.12"].opt_bin}/python3" -B "#{libexec}/dedup.py" "$@"
    EOS
  end

  test do
    system bin/"dedup", "--help"
  end
end
