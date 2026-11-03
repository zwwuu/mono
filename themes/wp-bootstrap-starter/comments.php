<?php
/**
 * Comments area. Markup for each comment lives in wbs_comment()
 * (see inc/template-tags.php).
 *
 * @package wp-bootstrap-starter
 */

defined( 'ABSPATH' ) || exit;

if ( post_password_required() ) {
	return;
}
?>
<div id="comments" class="comments-area mt-5">
	<?php if ( have_comments() ) : ?>
		<h2 class="h4 mb-4">
			<?php
			printf(
				/* translators: %s: number of comments. */
				esc_html( _nx( 'One comment', '%1$s comments', get_comments_number(), 'comments title', WBS_TEXTDOMAIN ) ),
				esc_html( number_format_i18n( get_comments_number() ) )
			);
			?>
		</h2>

		<ol class="comment-list list-unstyled">
			<?php
			wp_list_comments(
				array(
					'style'       => 'ol',
					'short_ping'  => true,
					'callback'    => 'wbs_comment',
					'avatar_size' => 48,
				)
			);
			?>
		</ol>

		<?php
		the_comments_navigation(
			array(
				'prev_text' => esc_html__( 'Older comments', WBS_TEXTDOMAIN ),
				'next_text' => esc_html__( 'Newer comments', WBS_TEXTDOMAIN ),
			)
		);
		?>
	<?php endif; ?>

	<?php if ( ! comments_open() && get_comments_number() && post_type_supports( get_post_type(), 'comments' ) ) : ?>
		<p class="alert alert-warning"><?php esc_html_e( 'Comments are closed.', WBS_TEXTDOMAIN ); ?></p>
	<?php endif; ?>

	<?php
	comment_form(
		array(
			'class_submit'  => 'btn btn-primary',
			'comment_field' => '<p class="comment-form-comment"><label for="comment" class="form-label">' . esc_html_x( 'Comment', 'noun', WBS_TEXTDOMAIN ) . '</label><textarea id="comment" name="comment" class="form-control" rows="5" required></textarea></p>',
		)
	);
	?>
</div>
