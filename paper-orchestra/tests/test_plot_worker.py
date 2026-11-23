"""execute_plot_code_worker runs the plotting code an LLM wrote.

What went wrong before:
- code ending in plt.savefig("x.png"); plt.close() left no open figure, so the
  figure was lost, and x.png landed in the backend's working folder (on the
  owner's PC: many stray *.png/*.pdf files in the repo root);
- code under `if __name__ == "__main__":` never ran;
- up to five figures are drawn at once in threads that share pyplot, so one
  thread's plt.close("all") could wipe another's figure.

The fix must not change what already worked: plt.close() really closes (so the
current figure and figure numbers behave as usual), and a file the code saves
can be read back while the code runs.
"""

import base64
import builtins
import contextlib
import io
import os
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backend_bases import FigureCanvasBase  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from PIL import Image  # noqa: E402

import utils.paper_banana_utils as pbu  # noqa: E402
from utils.paper_banana_utils import execute_plot_code_worker  # noqa: E402


def image_size(b64):
    return Image.open(io.BytesIO(base64.b64decode(b64))).size


def darkest(b64):
    return Image.open(io.BytesIO(base64.b64decode(b64))).convert("L").getextrema()[0]


class PlotWorkerTests(unittest.TestCase):
    def setUp(self):
        self.originals = self.patchable()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.cwd = tmp.name
        old = os.getcwd()
        os.chdir(self.cwd)
        self.addCleanup(os.chdir, old)

    @staticmethod
    def patchable():
        return (
            plt.close,
            plt.show,
            plt.pause,
            plt.savefig,
            Figure.savefig,
            Figure.clear,
            Figure.clf,
            FigureCanvasBase.start_event_loop,
            builtins.input,
            list(sys.argv),
            sys.argv,
        )

    def tearDown(self):
        self.assertEqual(self.patchable(), self.originals)

    def test_figure_saved_to_a_file_and_closed_is_still_captured(self):
        code = (
            "```python\n"
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots(figsize=(4, 3))\n"
            "ax.plot([1, 2, 3], [2, 4, 3])\n"
            "plt.savefig('architecture_diagram.png', dpi=300)\n"
            "fig.savefig('teaser.pdf')\n"
            "plt.show()\n"
            "plt.close(fig)\n"
            "```"
        )
        out = execute_plot_code_worker(code)
        self.assertIsNotNone(out)
        self.assertEqual(base64.b64decode(out)[:3], b"\xff\xd8\xff")  # JPEG
        self.assertEqual(os.listdir(self.cwd), [])  # nothing written next to the backend

    def test_code_under_a_main_guard_runs(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "def main():\n"
            "    plt.figure(figsize=(3, 3))\n"
            "    plt.bar(['a', 'b'], [1, 2])\n"
            "if __name__ == '__main__':\n"
            "    main()\n"
        )
        self.assertIsNotNone(execute_plot_code_worker(code))

    def test_saving_into_a_buffer_still_works_inside_the_code(self):
        code = (
            "import io\nimport matplotlib.pyplot as plt\n"
            "plt.plot([0, 1])\nbuf = io.BytesIO()\nplt.savefig(buf, format='png')\n"
            "assert buf.getvalue()[:4] == b'\\x89PNG'\n"
        )
        self.assertIsNotNone(execute_plot_code_worker(code))

    def test_broken_code_returns_none_and_restores_pyplot(self):
        self.assertIsNone(execute_plot_code_worker("import matplotlib.pyplot as plt\nplt.plot([1])\n1/0\n"))
        self.assertEqual(plt.get_fignums(), [])
        # code that draws nothing
        self.assertIsNone(execute_plot_code_worker("x = 1\n"))

    def test_sys_exit_and_argparse_under_the_main_guard(self):
        for tail in ("    sys.exit(main())\n", "    raise SystemExit(main())\n", "    main()\n    sys.exit(0)\n"):
            code = (
                "import sys, argparse\nimport matplotlib.pyplot as plt\n"
                "def main():\n"
                "    args = argparse.ArgumentParser().parse_args()\n"
                "    plt.plot([1, 3, 2])\n"
                "if __name__ == '__main__':\n" + tail
            )
            self.assertIsNotNone(execute_plot_code_worker(code), tail)
        # A real failure exit is a failed plot, not the end of the whole run.
        self.assertIsNone(execute_plot_code_worker("import sys\nimport matplotlib.pyplot as plt\nplt.plot([1])\nsys.exit(2)\n"))
        self.assertIsNone(execute_plot_code_worker("raise SystemExit('no data')\n"))

    def test_closing_a_helper_figure_keeps_the_main_one(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots(figsize=(8, 3))\nax.bar(['a', 'b', 'c'], [3, 1, 2])\n"
            "lf = plt.figure(figsize=(2, 2))\nlax = lf.add_axes([0, 0, 1, 1])\nlax.axis('off')\n"
            "lax.add_patch(plt.Rectangle((0, 0), 1, 1, color='k'))\n"
            "lf.savefig('legend.png')\nplt.close(lf)\n"
        )
        w, h = image_size(execute_plot_code_worker(code))
        self.assertGreater(w, 2 * h)  # the wide 8x3 chart, not the square helper
        self.assertEqual(os.listdir(self.cwd), [])

    def test_a_closed_figure_number_starts_fresh(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "plt.figure(1, figsize=(2, 2))\nplt.plot([0, 1])\nplt.close(1)\n"
            "plt.figure(1, figsize=(8, 3))\nplt.bar(['x', 'y'], [1, 2])\n"
        )
        w, h = image_size(execute_plot_code_worker(code))
        self.assertGreater(w, 2 * h)

    def test_code_can_read_back_the_file_it_saved(self):
        code = (
            "import os\nimport matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots(figsize=(3, 3))\nax.plot([1, 2])\n"
            "fig.savefig('part.png')\nplt.close(fig)\n"
            "assert os.path.getsize('part.png') > 0\n"
            "img = plt.imread('part.png')\n"
            "fig2, ax2 = plt.subplots(figsize=(6, 3))\nax2.imshow(img)\nax2.axis('off')\n"
            "plt.savefig('final')\n"  # no extension: matplotlib writes final.png
        )
        out = execute_plot_code_worker(code)
        self.assertIsNotNone(out)
        self.assertEqual(os.listdir(self.cwd), [])

    def test_saving_then_clearing_still_gives_the_drawn_figure(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "plt.figure(figsize=(4, 3))\nplt.fill_between([0, 1], [0, 0], [1, 1], color='k')\n"
            "plt.savefig('x.png')\nplt.clf()\n"
        )
        out = execute_plot_code_worker(code)
        self.assertIsNotNone(out)
        self.assertLess(darkest(out), 60)  # not a blank white canvas

    def test_existing_files_are_left_as_they_were(self):
        with open("figure.png", "wb") as fh:
            fh.write(b"mine")
        os.mkdir("plots")
        code = (
            "import matplotlib.pyplot as plt\nplt.plot([1, 2])\n"
            "plt.savefig('figure.png')\nplt.savefig('plots/a.pdf')\nplt.savefig('missing_dir/b.png')\n"
        )
        self.assertIsNotNone(execute_plot_code_worker(code))
        with open("figure.png", "rb") as fh:
            self.assertEqual(fh.read(), b"mine")
        self.assertEqual(sorted(os.listdir(self.cwd)), ["figure.png", "plots"])
        self.assertEqual(os.listdir("plots"), [])

    def test_show_then_close_keeps_the_figure(self):
        code = (
            "import io\nimport matplotlib.pyplot as plt\n"
            "h = plt.figure(figsize=(1, 1))\nh.text(0.1, 0.5, 'x')\nh.savefig(io.BytesIO(), format='png')\nplt.close(h)\n"
            "fig, ax = plt.subplots(figsize=(6, 3))\nax.bar(['a', 'b'], [1, 2])\nplt.show()\nplt.close(fig)\n"
        )
        w, h = image_size(execute_plot_code_worker(code))
        self.assertGreater(w, 1.5 * h)  # the 6x3 chart, not the 1x1 helper

    def test_a_legend_saved_after_the_chart_does_not_replace_it(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots(figsize=(8, 3))\nbars = ax.bar(['a', 'b'], [1, 2], label='v')\n"
            "fig.savefig('main.png')\nplt.close(fig)\n"
            "lf = plt.figure(figsize=(2, 2))\nlax = lf.add_axes([0, 0, 1, 1])\nlax.axis('off')\n"
            "lax.add_patch(plt.Rectangle((0, 0), 1, 1, color='k'))\nlf.savefig('legend.png')\nplt.close(lf)\n"
        )
        w, h = image_size(execute_plot_code_worker(code))
        self.assertGreater(w, 2 * h)
        self.assertEqual(os.listdir(self.cwd), [])

    def test_a_main_block_that_needs_arguments_falls_back_to_the_top_level_figure(self):
        for guard in (
            "    import argparse\n    p = argparse.ArgumentParser()\n    p.add_argument('--out', required=True)\n"
            "    plt.savefig(p.parse_args().out)\n",
            "    plt.savefig(sys.argv[1])\n",
        ):
            code = (
                "import sys\nimport matplotlib.pyplot as plt\n"
                "plt.figure(figsize=(4, 3))\nplt.plot([1, 3, 2])\n"
                "if __name__ == '__main__':\n" + guard
            )
            self.assertIsNotNone(execute_plot_code_worker(code), guard)
        self.assertEqual(os.listdir(self.cwd), [])

    def test_a_name_ending_in_a_dot_is_cleaned_up_too(self):
        with open("fig.png", "wb") as fh:
            fh.write(b"mine")
        self.assertIsNotNone(execute_plot_code_worker("import matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('fig.')\nplt.savefig('other.')\n"))
        with open("fig.png", "rb") as fh:
            self.assertEqual(fh.read(), b"mine")
        self.assertEqual(os.listdir(self.cwd), ["fig.png"])

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
    def test_an_existing_link_is_not_removed(self):
        os.symlink("target.png", "link.png")
        self.assertIsNotNone(execute_plot_code_worker("import matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('link.png')\n"))
        self.assertTrue(os.path.islink("link.png"))

    def run_and_capture_stdout(self, code):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = execute_plot_code_worker(code)
        return result, out.getvalue()

    def test_closed_figures_are_not_kept_alive(self):
        code = (
            "import gc, weakref\nimport matplotlib.pyplot as plt\nrefs = []\n"
            "for i in range(4):\n"
            "    f = plt.figure(figsize=(3, 2))\n    plt.plot([0, i])\n"
            "    f.savefig(f'frame{i}.png')\n    refs.append(weakref.ref(f))\n    plt.close(f)\n"
            "del f\ngc.collect()\nassert sum(r() is not None for r in refs) == 0, 'closed figures still alive'\n"
        )
        self.assertIsNotNone(execute_plot_code_worker(code))
        self.assertEqual(os.listdir(self.cwd), [])

    def test_the_code_runs_once_even_when_its_main_block_fails(self):
        code = (
            "import os, argparse\nimport matplotlib.pyplot as plt\n"
            "print('loading data')\nos.mkdir('plot_outputs')\n"
            "fig, ax = plt.subplots(figsize=(4, 3))\nax.bar(['a', 'b'], [1, 2])\n"
            "if __name__ == '__main__':\n"
            "    p = argparse.ArgumentParser()\n    p.add_argument('--out', required=True)\n"
            "    extra = plt.figure(figsize=(9, 9))\n"
            "    fig.savefig(p.parse_args().out)\n"
        )
        with contextlib.redirect_stderr(io.StringIO()):
            out, printed = self.run_and_capture_stdout(code)
        w, h = image_size(out)
        self.assertGreater(w, h)  # the 4x3 bar chart, not the 9x9 figure from the failed block
        self.assertEqual(printed.count("loading data"), 1)
        self.assertIn("__main__ block", printed)

    def test_a_top_level_error_is_not_blamed_on_the_main_block(self):
        for code in (
            "import matplotlib.pyplot as plt\nprint('loading data')\nplt.plot(undefined_name)\n"
            "if __name__ == '__main__':\n    plt.show()\n",
            "# run with: if __name__ == '__main__'\nprint('loading data')\nundefined_name\n",
        ):
            out, printed = self.run_and_capture_stdout(code)
            self.assertIsNone(out)
            self.assertEqual(printed.count("loading data"), 1)
            self.assertNotIn("__main__ block", printed)

    def test_show_then_close_all_with_a_helper_made_last(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots(figsize=(8, 3))\nax.bar(['a', 'b'], [1, 2], label='v')\n"
            "lf = plt.figure(figsize=(2, 2))\nlf.legend(*ax.get_legend_handles_labels())\n"
            "plt.show()\nplt.close('all')\n"
        )
        w, h = image_size(execute_plot_code_worker(code))
        self.assertGreater(w, 2 * h)

    def test_a_scratch_figure_does_not_beat_the_saved_one(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "scratch = plt.figure(figsize=(9, 9))\nscratch.text(0.5, 0.5, 'measure')\nplt.close(scratch)\n"
            "fig, ax = plt.subplots(figsize=(6, 2))\nax.plot([1, 2, 3])\nfig.savefig('out.png')\nplt.close(fig)\n"
        )
        w, h = image_size(execute_plot_code_worker(code))
        self.assertGreater(w, 2 * h)

    def test_drawn_then_closed_without_saving_still_gives_the_last_drawing(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "fig = plt.figure(figsize=(2, 2))\nplt.plot([0, 1])\nfig.clf()\n"  # a draft, cleared
            "ax = fig.add_axes([0, 0, 1, 1])\nax.axis('off')\nax.add_patch(plt.Rectangle((0, 0), 1, 1, color='k'))\n"
            "plt.close(fig)\n"
        )
        out = execute_plot_code_worker(code)
        self.assertIsNotNone(out)
        self.assertLess(darkest(out), 60)  # the black square, not the white draft

    def test_a_failing_main_block_keeps_the_figure_drawn_before_it(self):
        chart = "import os, argparse\nimport matplotlib.pyplot as plt\nfig, ax = plt.subplots(figsize=(7, 3))\nax.bar(['a', 'b'], [1, 2], color='k')\n"
        for block in (
            "    plt.savefig('results/figure1.png')\n    plt.close()\n    print(os.path.getsize('results/figure1.png'))\n",
            "    plt.show()\n    plt.close(fig)\n    from no_such_module import files\n",
            "    plt.clf()\n    p = argparse.ArgumentParser()\n    p.add_argument('--out', required=True)\n    p.parse_args()\n",
        ):
            with contextlib.redirect_stderr(io.StringIO()):
                out, printed = self.run_and_capture_stdout(chart + "if __name__ == '__main__':\n" + block)
            self.assertIsNotNone(out, block)
            w, h = image_size(out)
            self.assertGreater(w, 1.5 * h, block)
            self.assertLess(darkest(out), 60, block)  # the chart, not a blank canvas
        self.assertEqual(os.listdir(self.cwd), [])

    def test_other_spellings_of_the_main_guard(self):
        top = "import sys\nimport matplotlib.pyplot as plt\nplt.figure(figsize=(4, 3))\nplt.plot([1, 3, 2])\n"
        for guard in (
            "if __name__ in ('__main__',):\n    plt.savefig(sys.argv[1])\n",
            "if '__main__' == __name__ and True:\n    plt.savefig(sys.argv[1])\n",
            "try:\n    if __name__ == '__main__':\n        plt.savefig(sys.argv[1])\nexcept KeyboardInterrupt:\n    pass\n",
        ):
            out, _ = self.run_and_capture_stdout(top + guard)
            self.assertIsNotNone(out, guard)
        # Inside a function it is ordinary code: an error there is a real failure.
        out, _ = self.run_and_capture_stdout(top + "def f():\n    if __name__ == '__main__':\n        return sys.argv[1]\nf()\n")
        self.assertIsNone(out)

    def test_pylab_style_code_leaves_nothing_patched(self):
        self.assertIsNotNone(execute_plot_code_worker("from pylab import *\nfigure()\nplot([1, 2])\nsavefig('a.png')\nclose()\n"))
        for name in ("pylab", "matplotlib.pylab"):
            mod = sys.modules.get(name)
            if mod is not None:
                self.assertIs(mod.close, plt.close)
                self.assertIs(mod.show, plt.show)
        self.assertIsNotNone(execute_plot_code_worker("import pylab as pl\npl.figure(figsize=(6, 3))\npl.plot([1, 2, 3])\npl.show()\npl.close()\n"))

    def test_saving_a_new_blank_figure_does_not_replace_the_chart(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots(figsize=(7, 3.5))\nax.bar(['a', 'b'], [1, 2], color='k')\n"
            "fig.savefig('fig.png')\nplt.close(fig)\nplt.savefig('fig.pdf')\nplt.close()\n"
        )
        out = execute_plot_code_worker(code)
        self.assertIsNotNone(out)
        self.assertLess(darkest(out), 60)
        self.assertEqual(os.listdir(self.cwd), [])

    def test_an_existing_file_is_restored_even_if_the_code_deletes_it(self):
        with open("tmp_plot.png", "wb") as fh:
            fh.write(b"user file")
        code = "import os\nimport matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('tmp_plot.png')\nimg = plt.imread('tmp_plot.png')\nos.remove('tmp_plot.png')\n"
        self.assertIsNotNone(execute_plot_code_worker(code))
        with open("tmp_plot.png", "rb") as fh:
            self.assertEqual(fh.read(), b"user file")

    def test_a_figure_left_open_is_rendered_only_once(self):
        calls = []
        real = pbu._capture_jpeg

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        pbu._capture_jpeg = counting
        try:
            code = "import matplotlib.pyplot as plt\nplt.figure(figsize=(4, 3))\nplt.plot([1, 2])\nplt.savefig('a.pdf')\nplt.savefig('a.png')\nplt.show()\n"
            self.assertIsNotNone(execute_plot_code_worker(code))
            self.assertEqual(len(calls), 1)
            # Scratch figures closed after a saved one can never win, so they are not rendered.
            calls.clear()
            code = (
                "import matplotlib.pyplot as plt\nfig = plt.figure(figsize=(4, 3))\nplt.plot([1, 2])\n"
                "fig.savefig('a.png')\nplt.close(fig)\n"
                "for i in range(10):\n    plt.figure(figsize=(4, 3))\n    plt.plot([0, i])\n    plt.close()\n"
            )
            self.assertIsNotNone(execute_plot_code_worker(code))
            self.assertEqual(len(calls), 1)
        finally:
            pbu._capture_jpeg = real

    def test_helper_figures_without_plotted_data_lose_to_the_chart(self):
        chart = "import io\nimport matplotlib.pyplot as plt\nfig, ax = plt.subplots(figsize=(3.5, 2.5))\nbars = ax.bar(['a', 'b'], [1, 2], color='k', label='v')\n"
        for tail in (
            # a shared legend saved after the chart
            "fig.savefig('main.pdf')\nplt.close(fig)\nlf = plt.figure()\nlf.legend([bars], ['v'], ncol=4)\nlf.savefig('legend.pdf')\nplt.close(lf)\n",
            # a legend strip drawn on hidden axes
            "plt.close(fig)\nfig.savefig('main.png')\nlf, lax = plt.subplots(figsize=(4, 0.6))\nlax.axis('off')\nlax.legend([bars], ['v'])\nlf.savefig('legend.png')\nplt.close(lf)\n",
            # a big text-measuring figure left open, then show and close everything
            "m = plt.figure(figsize=(10, 10))\nm.text(0.1, 0.5, 'measure me')\nplt.show()\nplt.close('all')\n",
        ):
            out = execute_plot_code_worker(chart + tail)
            self.assertIsNotNone(out, tail)
            w, h = image_size(out)
            self.assertLess(darkest(out), 60, tail)
            self.assertGreater(h, 300, tail)  # not a thin legend or text strip
        self.assertEqual(os.listdir(self.cwd), [])

    def test_a_saved_chart_beats_a_bigger_figure_that_was_only_shown(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "plt.figure(figsize=(8, 8))\nplt.hist([1, 2, 2, 3, 3, 3])\n"
            "fig, ax = plt.subplots(figsize=(6, 2))\nax.plot([1, 2, 3], color='k')\nfig.savefig('final.png')\n"
            "plt.show()\nplt.close('all')\n"
        )
        w, h = image_size(execute_plot_code_worker(code))
        self.assertGreater(w, 2 * h)

    def test_a_failing_main_block_that_reworked_the_figure_falls_back_to_it(self):
        chart = "import sys\nimport numpy as np\nimport matplotlib.pyplot as plt\nfig, ax = plt.subplots(num=1, figsize=(6, 3))\nax.bar(['a', 'b'], [1, 2], color='k')\n"
        for block in (
            "    big = plt.figure(figsize=(10, 6))\n    plt.plot([0, 1])\n    big.savefig('supp.png')\n    plt.close(big)\n    plt.close(fig)\n    open('missing/results.csv')\n",
            "    plt.clf()\n    ax = plt.gca()\n    ax.plot(np.loadtxt(sys.argv[1]))\n",
            "    fig.clf()\n    fig.add_subplot(111).set_title('reloaded')\n    plt.close(fig)\n    raise RuntimeError('no data')\n",
            "    fig = plt.figure(1, clear=True)\n    fig.add_subplot(111)\n    np.load('missing.npy')\n",
        ):
            out, _ = self.run_and_capture_stdout(chart + "if __name__ == '__main__':\n" + block)
            self.assertIsNotNone(out, block)
            w, h = image_size(out)
            self.assertGreater(w, 1.5 * h, block)
            self.assertLess(darkest(out), 60, block)
        self.assertEqual(os.listdir(self.cwd), [])

    def test_a_figure_pyplot_does_not_manage_is_kept_when_saved(self):
        code = (
            "from matplotlib.figure import Figure\nfig = Figure(figsize=(6, 3))\nax = fig.subplots()\n"
            "ax.bar(['a', 'b'], [1, 2], color='k')\nfig.savefig('oo_figure.png')\n"
        )
        out = execute_plot_code_worker(code)
        self.assertIsNotNone(out)
        self.assertLess(darkest(out), 60)
        self.assertEqual(os.listdir(self.cwd), [])

    @unittest.skipUnless(hasattr(os, "symlink") and os.name != "nt", "needs symlinks")
    def test_one_file_saved_under_two_names_is_cleaned_up(self):
        os.mkdir("real")
        os.symlink("real", "alias")
        code = "import matplotlib.pyplot as plt\nplt.plot([1])\nplt.savefig('real/fig.png')\nplt.savefig('alias/fig.png')\n"
        self.assertIsNotNone(execute_plot_code_worker(code))
        self.assertEqual(os.listdir("real"), [])

    def test_closing_many_figures_renders_only_what_can_win(self):
        calls = []
        real = pbu._capture_jpeg

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        pbu._capture_jpeg = counting
        try:
            code = (
                "import matplotlib.pyplot as plt\n"
                "for i in range(8):\n    plt.figure(figsize=(6, 4))\n    plt.plot([0, i])\n"
                "plt.savefig('x.png')\nplt.close('all')\n"
            )
            self.assertIsNotNone(execute_plot_code_worker(code))
            self.assertEqual(len(calls), 1)
        finally:
            pbu._capture_jpeg = real

    def test_closing_equal_saved_figures_renders_once_and_keeps_the_last(self):
        calls = []
        real = pbu._capture_jpeg

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        pbu._capture_jpeg = counting
        try:
            code = (
                "import matplotlib.pyplot as plt\n"
                "for i in range(6):\n"
                "    fig, ax = plt.subplots(figsize=(6, 4))\n"
                "    ax.axis('off')\n"
                "    ax.plot([0, 1], [0, 1], color='black' if i == 5 else 'tab:blue')\n"
                "    fig.savefig(f'panel{i}.png')\n"
                "plt.close('all')\n"
            )
            out = execute_plot_code_worker(code)
            self.assertEqual(len(calls), 1)
            self.assertLess(darkest(out), 40)  # the black line of the last panel
            self.assertEqual(os.listdir(self.cwd), [])
        finally:
            pbu._capture_jpeg = real

    def run_with_deadline(self, code, seconds=30):
        box = {}

        def run():
            box["out"], box["printed"] = self.run_and_capture_stdout(code)

        start = time.monotonic()
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        thread.join(seconds)
        self.assertFalse(thread.is_alive(), "the plot code is still waiting")
        return box["out"], box["printed"], time.monotonic() - start

    def test_nothing_waits_for_a_click_or_a_pause(self):
        code = (
            "import matplotlib.pyplot as plt\n"
            "fig, ax = plt.subplots(figsize=(6, 3))\n"
            "ax.bar(['a', 'b'], [1, 2])\n"
            "if __name__ == '__main__':\n"
            "    plt.savefig('fig.png')\n"
            "    plt.waitforbuttonpress()\n"
            "    fig.waitforbuttonpress(timeout=-1)\n"
            "    points = plt.ginput(2)\n"
            "    plt.pause(30)\n"
            "    plt.show(block=True)\n"
        )
        out, printed, elapsed = self.run_with_deadline(code)
        self.assertNotIn("failed", printed)
        self.assertEqual(image_size(out), image_size(execute_plot_code_worker(code.split("if __name__")[0])))
        self.assertLess(elapsed, 15)
        self.assertEqual(os.listdir(self.cwd), [])

    def test_pause_without_a_figure_does_not_sleep(self):
        code = "import matplotlib.pyplot as plt\nplt.pause(30)\nplt.plot([1, 2])\n"
        out, _, elapsed = self.run_with_deadline(code)
        self.assertIsNotNone(out)
        self.assertLess(elapsed, 15)

    def test_input_fails_at_once_instead_of_waiting(self):
        read_end, write_end = os.pipe()  # a stdin nobody will ever type into
        old_stdin, sys.stdin = sys.stdin, os.fdopen(read_end)
        self.addCleanup(os.close, write_end)
        self.addCleanup(sys.stdin.close)
        self.addCleanup(setattr, sys, "stdin", old_stdin)
        chart = "import matplotlib.pyplot as plt\nplt.figure(figsize=(5, 3))\nplt.bar(['a', 'b'], [1, 2])\n"
        out, printed, elapsed = self.run_with_deadline(
            chart + "if __name__ == '__main__':\n    plt.title(input('Title? '))\n"
        )
        self.assertIn("EOFError", printed)
        self.assertEqual(image_size(out), image_size(execute_plot_code_worker(chart)))
        self.assertLess(elapsed, 15)
        out, _, _ = self.run_with_deadline(chart + "plt.title(__builtins__.input('Title? '))\n")
        self.assertIsNone(out)  # at top level it fails like any other error

    def test_code_that_removes_a_patched_method_leaves_nothing_patched(self):
        code = (
            "import matplotlib.pyplot as plt\nfrom matplotlib.figure import Figure\n"
            "del Figure.clf\n"
            "plt.plot([1, 2])\nplt.savefig('left.png')\nplt.clf()\nplt.plot([2, 1])\n"
        )
        self.assertIsNotNone(execute_plot_code_worker(code))
        self.assertEqual(os.listdir(self.cwd), [])  # tearDown checks the rest

    def test_parallel_figures_do_not_wipe_each_other(self):
        sizes = [(2, 2), (3, 2), (4, 2), (5, 2), (6, 2)]
        results = {}

        def draw(i, w, h):
            code = (
                "import matplotlib.pyplot as plt\n"
                f"fig = plt.figure(figsize=({w}, {h}))\n"
                "plt.close('all')\n"  # what generated code sometimes does first
                f"fig = plt.figure(figsize=({w}, {h}))\n"
                "ax = fig.add_axes([0, 0, 1, 1])\nax.axis('off')\n"
                "ax.add_patch(plt.Rectangle((0, 0), 1, 1, color='k'))\n"
                f"plt.savefig('fig{i}.png')\nplt.close()\n"
            )
            results[i] = execute_plot_code_worker(code)

        threads = [threading.Thread(target=draw, args=(i, w, h)) for i, (w, h) in enumerate(sizes)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        widths = [image_size(results[i])[0] for i in range(len(sizes))]
        # each thread got its own figure back: wider figure, wider image
        self.assertEqual(widths, sorted(widths))
        self.assertEqual(len(set(widths)), len(sizes))
        self.assertEqual(os.listdir(self.cwd), [])


if __name__ == "__main__":
    unittest.main()
